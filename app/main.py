"""
Module C: FastAPI wiring.

Wraps the already-proven Module A (tle_fetch, risk_score) + Module B
(cost_matrix, optimizer) pipeline behind three endpoints. No new math or
logic lives here -- this is glue.

Pipeline for /plan:
  get_debris_field()        -> raw debris in the 700-1000km band
  score_debris_field()      -> adds proximity/lifetime/risk_score, sorted
  select_candidate_pool()   -> top-N by risk_score
  optimize_route()          -> builds its OWN cost matrix internally
                               (depot + pool + virtual end node), so main.py
                               never touches build_cost_matrix/scale_matrix_
                               for_ortools directly -- optimizer.py already
                               owns that.
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import groq as groq_module
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

load_dotenv()

from app.tle_fetch import get_debris_field, get_cache_timestamp, CACHE_MAX_AGE_SECONDS
from app.launch_sites import LAUNCH_SITES, derive_start_orbit
from app.risk_score import score_debris_field, DEFAULT_WEIGHTS
from app.cost_matrix import select_candidate_pool, DEFAULT_POOL_SIZE, DELTA_V_SCALE
from app.optimizer import optimize_route, solve_forced_route, TRANSFER_TIME_DAYS_PER_KM_S
from app.removal_method import add_removal_methods, METHOD_ROBOTIC_ARM_OR_NET, METHOD_NET_CAPTURE, METHOD_MONITOR_ONLY
from fastapi.middleware.cors import CORSMiddleware

# monitor_only excluded: it's never a real route target (see cost_matrix.
# select_candidate_pool), so it's not a legal removal_method_filter value.
_VALID_REMOVAL_METHOD_FILTERS = {METHOD_ROBOTIC_ARM_OR_NET, METHOD_NET_CAPTURE}

# TLE data-quality thresholds (days from epoch).
# Grounded in published TLE-accuracy literature: position error grows
# ~1-3 km/day from epoch; the commonly cited reliable window is ~2 weeks.
# "fresh" < 7 days, "aging" 7-14 days, "stale" > 14 days.
_TLE_FRESH_DAYS = 7.0
_TLE_AGING_DAYS = 14.0


def _data_quality(epoch_age_days: float) -> str:
    """Map epoch age (days) to a human-readable data-quality label.
    Thresholds are grounded in published TLE-accuracy research -- do not
    change without updating the Field description on max_tle_age_days."""
    if epoch_age_days < _TLE_FRESH_DAYS:
        return "fresh"
    if epoch_age_days < _TLE_AGING_DAYS:
        return "aging"
    return "stale"

# Module-level cache for removal method explanations.  Key is removal_method
# (bare string) -- only 3 distinct values exist from removal_method.py, so
# this cache is bounded at 3 entries and never grows unbounded.  Cached for
# the process lifetime: the explanation is generic to the technique, not to
# any specific object or live orbital state, so it never needs invalidation.
_REMOVAL_METHOD_EXPLANATION_CACHE: dict[str, tuple[str, str]] = {}

# M3: in-memory cache for the default scored + enriched field (no custom weights).
# Keyed by the TLE cache file's mtime string so it auto-invalidates whenever
# get_debris_field() writes a fresh fetch.  Custom-weights calls (from /plan or
# /replan with non-default weights) bypass this cache because their output
# depends on the caller-supplied weights dict and must not cross-contaminate.
_scored_field_cache: dict[str, list] = {}  # {"<mtime>": [enriched_objects]}

logger = logging.getLogger(__name__)

app = FastAPI(title="Orbital-Clean API")

# H3: read allowed origins from env var so deployed environments work without
# code changes.  Comma-separated list, e.g.:
#   ALLOWED_ORIGINS=https://my-app.example.com,http://localhost:5173
_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/launch-sites")
def launch_sites_catalog():
    """Return the fixed launch-site catalog for frontend consumption.
    Each entry contains name, lat, lon, and min_inclination."""
    return {
        key: {
            "name":            site["name"],
            "lat":             site["lat"],
            "lon":             site["lon"],
            "min_inclination": site["min_inclination"],
        }
        for key, site in LAUNCH_SITES.items()
    }


def _get_scored_field(force_refresh: bool = False, weights: Optional[dict[str, float]] = None) -> list[dict[str, Any]]:
    """Shared by /debris-field, /debris/{norad_id}, and /plan/replan so
    there's one place that does fetch+score+classify. object_type/
    removal_method are added HERE, once, on the full scored field --
    not per-pool inside /plan -- so a given norad_id gets the same
    classification everywhere it appears. add_removal_methods()'s bstar
    threshold is batch-relative (median among fragments in whatever's
    passed in); computing it per-pool instead would make the threshold
    drift with pool_size/weights and disagree across endpoints for the
    same object.

    M3: when called with default weights (weights=None, the common case for
    /debris-field, /debris/{id}, and the first /plan of a session), the result
    is cached in _scored_field_cache keyed by the TLE file's mtime.  Custom-
    weights calls bypass the cache so scoring is always fresh for those."""
    # M3: serve from in-memory cache when using default weights and no forced refresh
    cache_key: str = ""
    use_cache = (weights is None and not force_refresh)
    if use_cache:
        try:
            import app.tle_fetch as _tf
            if os.path.exists(_tf.CACHE_FILE):
                cache_key = str(os.path.getmtime(_tf.CACHE_FILE))
                if cache_key in _scored_field_cache:
                    logger.debug("[_get_scored_field] cache hit for mtime=%s", cache_key)
                    return _scored_field_cache[cache_key]
        except Exception:
            pass  # cache miss is always safe — fall through to the real pipeline

    raw = get_debris_field(force_refresh=force_refresh)
    scored = score_debris_field(raw, weights=weights or DEFAULT_WEIGHTS)
    enriched = add_removal_methods(scored)
    for obj in enriched:
        # data_quality is unconditional: every object gets the label
        # regardless of which endpoint is calling or what filters are active.
        # This gives /debris-field and /debris/{norad_id} full transparency,
        # and gives _run_plan() a stable field to filter on.
        obj["data_quality"] = _data_quality(obj.get("epoch_age_days", 0.0))
        explanation, source = _explain_removal_method(
            obj["removal_method"],
            obj.get("possible_methods", []),
            obj.get("method_maturity", {}),
        )
        obj["removal_method_explanation"] = explanation
        obj["removal_method_explanation_source"] = source

    # M3: populate cache only for default-weights results
    if use_cache and cache_key:
        _scored_field_cache.clear()   # one entry only — old mtime is stale
        _scored_field_cache[cache_key] = enriched

    return enriched


class PlanRequest(BaseModel):
    # --- launch-site alternative input (mutually exclusive with raw start fields) ---
    launch_site: Optional[str] = Field(
        None,
        description=(
            "Launch site key, one of: "
            + ", ".join(sorted(LAUNCH_SITES))
            + ". Mutually exclusive with providing start_altitude_km/"
            "start_inclination_deg directly — supply one or the other."
        ),
    )
    inclination_deg: Optional[float] = Field(
        None,
        description=(
            "Desired orbital inclination (deg) when using launch_site. "
            "Clamped to site's min_inclination (= site latitude) if lower. "
            "Ignored when start_inclination_deg is supplied directly."
        ),
    )

    # --- raw start-orbit fields (required when launch_site is absent) ---
    start_altitude_km: Optional[float] = Field(
        None,
        description="Spacecraft's current orbit altitude, km. Required unless launch_site is supplied.",
    )
    start_inclination_deg: Optional[float] = Field(
        None,
        description="Spacecraft's current orbit inclination, deg. Required unless launch_site is supplied.",
    )
    start_raan_deg: float = Field(0.0, description="Spacecraft's current orbit RAAN, deg. Defaults to 0.0 if the caller doesn't know their spacecraft's current RAAN, which re-enables the pre-RAAN |incl1-incl2| approximation for depot hops only -- every debris-to-debris leg already uses real RAAN values from tle_fetch.py regardless.")
    fuel_budget_km_s: float = Field(..., gt=0, description="Total delta-v budget for the mission, km/s")
    pool_size: int = Field(DEFAULT_POOL_SIZE, gt=0, description="How many top-risk candidates the optimizer considers")
    weights: Optional[dict[str, float]] = Field(None, description="Override risk_score.py DEFAULT_WEIGHTS, e.g. {'proximity': 0.8, 'lifetime': 0.2}")
    nets_carried: int = Field(1, ge=1, description="Max net_capture stops in the route. Default 1 matches RemoveDEBRIS's actual flight history (it carried exactly one net) -- raise for an explicit exploratory what-if run.")
    removal_method_filter: Optional[str] = Field(None, description=f"Restrict the route to a single removal method -- one of {sorted(_VALID_REMOVAL_METHOD_FILTERS)}. No real ADR mission has flown mixed capture hardware (RemoveDEBRIS = net+harpoon only, ELSA-M = magnetic docking only), so this models 'one spacecraft, one hardware type'. Unset preserves the current mixed-method behavior.")
    target_norad_id: Optional[int] = Field(None, description="Force this object to be considered by the optimizer even if it wouldn't normally rank into the top pool_size by risk. The optimizer still decides whether to actually visit it (AddDisjunction still applies) -- this only guarantees consideration, not a visit. Rejected if the object is classified monitor_only (never a real target).")
    max_tle_age_days: float = Field(14.0, description="Exclude objects whose TLE epoch is older than this many days from route planning. Default 14.0 matches published TLE-accuracy research (~2 week reliable window). Raise to include older/less-trusted debris, lower to be stricter. Does not affect /debris-field or /debris/{norad_id}, which always show the full field with data_quality labels.")

    @model_validator(mode="before")
    @classmethod
    def resolve_launch_site(cls, data: Any) -> Any:
        """Resolve launch_site -> start_altitude/inclination/raan before field
        validation, or enforce that the raw fields are present when no site is
        given.  Idempotent: if start_altitude_km and start_inclination_deg are
        already populated (e.g. a second construction from model_dump()), the
        call is skipped even when launch_site is present."""
        if not isinstance(data, dict):
            return data

        has_site = data.get("launch_site") is not None
        has_alt  = data.get("start_altitude_km") is not None
        has_incl = data.get("start_inclination_deg") is not None

        if has_site and not (has_alt and has_incl):
            # Fresh launch-site resolution — raw fields not yet populated.
            orbit = derive_start_orbit(
                data["launch_site"],
                inclination=data.get("inclination_deg"),
                altitude_km=data.get("start_altitude_km") or 800,
            )
            data["start_altitude_km"]    = orbit["altitude_km"]
            data["start_inclination_deg"] = orbit["inclination_deg"]
            data["start_raan_deg"]        = orbit["raan_deg"]
        elif not has_site and not (has_alt and has_incl):
            raise ValueError(
                "Either launch_site or both start_altitude_km and "
                "start_inclination_deg must be provided."
            )
        # else: raw fields already present (raw-orbit path), or site + raw
        # both present (already resolved from a prior construction) — pass through.
        return data


class ReplanRequest(PlanRequest):
    user_request_text: str = Field(..., description="Plain-English override instructions, e.g. 'use only 1.5 km/s of fuel'")


class MissionCostRequest(BaseModel):
    """Request model for POST /mission-cost (Custom Selection mode).

    The caller has already decided which debris to visit; this endpoint answers
    "what does it cost to visit exactly these objects in the optimal order?"
    No fuel_budget_km_s — the point is to *report* the required fuel, not cap
    against one.  The start-orbit fields and validation mirror PlanRequest.
    """
    # --- launch-site alternative input (mutually exclusive with raw start fields) ---
    launch_site: Optional[str] = Field(
        None,
        description=(
            "Launch site key, one of: "
            + ", ".join(sorted(LAUNCH_SITES))
            + ". Mutually exclusive with providing start_altitude_km/"
            "start_inclination_deg directly — supply one or the other."
        ),
    )
    inclination_deg: Optional[float] = Field(
        None,
        description=(
            "Desired orbital inclination (deg) when using launch_site. "
            "Clamped to site's min_inclination if lower. "
            "Ignored when start_inclination_deg is supplied directly."
        ),
    )
    start_altitude_km: Optional[float] = Field(
        None,
        description="Spacecraft's current orbit altitude, km. Required unless launch_site is supplied.",
    )
    start_inclination_deg: Optional[float] = Field(
        None,
        description="Spacecraft's current orbit inclination, deg. Required unless launch_site is supplied.",
    )
    start_raan_deg: float = Field(
        0.0,
        description="Spacecraft's current orbit RAAN, deg. Defaults to 0.0 — see PlanRequest for full semantics.",
    )
    target_norad_ids: list[int] = Field(
        ...,
        min_length=1,
        description="NORAD IDs of the debris objects the user has already chosen to visit. Every ID must exist in the current debris field and none may be monitor_only.",
    )

    @model_validator(mode="before")
    @classmethod
    def resolve_launch_site(cls, data: Any) -> Any:
        """Same launch_site resolution logic as PlanRequest."""
        if not isinstance(data, dict):
            return data

        has_site = data.get("launch_site") is not None
        has_alt  = data.get("start_altitude_km") is not None
        has_incl = data.get("start_inclination_deg") is not None

        if has_site and not (has_alt and has_incl):
            orbit = derive_start_orbit(
                data["launch_site"],
                inclination=data.get("inclination_deg"),
                altitude_km=data.get("start_altitude_km") or 800,
            )
            data["start_altitude_km"]    = orbit["altitude_km"]
            data["start_inclination_deg"] = orbit["inclination_deg"]
            data["start_raan_deg"]        = orbit["raan_deg"]
        elif not has_site and not (has_alt and has_incl):
            raise ValueError(
                "Either launch_site or both start_altitude_km and "
                "start_inclination_deg must be provided."
            )
        return data


@app.get("/debris-field")
def debris_field(force_refresh: bool = False):
    """Full scored, risk-ranked debris list (riskiest first), with cache metadata."""
    field = _get_scored_field(force_refresh=force_refresh)
    fetched_at = get_cache_timestamp()
    age_seconds = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)).total_seconds()
    return {
        "debris_field": field,
        "data_fetched_at": fetched_at,
        "data_stale": age_seconds >= (CACHE_MAX_AGE_SECONDS - 600),  # within 10 min of refresh
    }


@app.get("/debris/{norad_id}")
def debris_detail(norad_id: int):
    """Single object lookup by NORAD catalog id."""
    scored = _get_scored_field()
    match = next((o for o in scored if o["norad_id"] == norad_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"norad_id {norad_id} not found in current 700-1000km band field")
    return match


def _run_plan(req: PlanRequest) -> dict[str, Any]:
    """Execute the full plan pipeline for a PlanRequest and return the result dict.
    Shared by /plan and /replan so both endpoints stay in sync."""
    scored = _get_scored_field(weights=req.weights)
    if not scored:
        raise HTTPException(status_code=502, detail="Debris field empty -- Celestrak fetch may have failed")

    if req.removal_method_filter is not None:
        if req.removal_method_filter not in _VALID_REMOVAL_METHOD_FILTERS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"removal_method_filter must be one of {sorted(_VALID_REMOVAL_METHOD_FILTERS)} "
                    f"or omitted, got {req.removal_method_filter!r}. monitor_only is not a legal "
                    "filter value -- those objects are never routable at all."
                ),
            )
        scored = [o for o in scored if o.get("removal_method") == req.removal_method_filter]

    # Exclude objects whose TLE is too old to trust for route planning.
    # data_quality label is always set upstream in _get_scored_field() --
    # we filter on the raw epoch_age_days value directly so the threshold
    # comparison is exact and not dependent on label string matching.
    # /debris-field and /debris/{norad_id} intentionally skip this filter
    # so users can see the full field with quality labels and decide for
    # themselves; exclusion only applies where bad data causes bad decisions.
    scored = [o for o in scored if o.get("epoch_age_days", 0.0) <= req.max_tle_age_days]

    # Filtering happens on `scored` (before pool selection) rather than
    # after, so select_candidate_pool's top-pool_size ranking is computed
    # over the already-restricted set -- otherwise a filter could silently
    # shrink the effective pool below pool_size even when plenty of
    # matching objects exist further down the risk ranking.
    pool = select_candidate_pool(scored, pool_size=req.pool_size)

    if req.target_norad_id is not None:
        target_obj = next((o for o in scored if o["norad_id"] == req.target_norad_id), None)
        if target_obj is None:
            raise HTTPException(
                status_code=404,
                detail=f"target_norad_id {req.target_norad_id} not found in the current debris field"
                + (" (or excluded by removal_method_filter)" if req.removal_method_filter else ""),
            )
        if target_obj.get("removal_method") == METHOD_MONITOR_ONLY:
            raise HTTPException(
                status_code=422,
                detail=f"target_norad_id {req.target_norad_id} is classified monitor_only -- "
                       "not a viable route target (see monitor_only pool exclusion).",
            )
        if not any(o["norad_id"] == req.target_norad_id for o in pool):
            pool = pool + [target_obj]  # guarantee consideration; still not a forced visit

    result = optimize_route(
        pool,
        fuel_budget_km_s=req.fuel_budget_km_s,
        start_altitude_km=req.start_altitude_km,
        start_inclination_deg=req.start_inclination_deg,
        start_raan_deg=req.start_raan_deg,
        nets_carried=req.nets_carried,
    )

    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    # OR-Tools can return a valid (non-None) solution that visits zero nodes
    # when the fuel budget is too tight to reach any candidate.  That is not
    # an error from the solver's perspective, so "error" is never set and the
    # guard above doesn't fire.  Flag it explicitly so callers always get a
    # human-readable explanation rather than a silent empty route.
    if result["visited_count"] == 0:
        min_hop = result["min_depot_hop_km_s"]
        result["warning"] = (
            f"No debris nodes were visited within the given constraints. "
            f"The cheapest depot hop on this pool is ~{min_hop} km/s -- "
            f"try raising fuel_budget_km_s above that value."
        )

    result["pool_size_used"] = len(pool)

    # Echo the depot position back so the frontend can draw the depot->first-debris
    # leg.  latitude/longitude default to 0.0 (equatorial crossing) -- the
    # spacecraft's real ground-track position isn't known from a PlanRequest alone
    # (we only have orbital elements), so 0/0 is the honest "unlocated" convention
    # used throughout the codebase rather than inventing a fake position.
    result["depot"] = {
        "altitude_km": req.start_altitude_km,
        "inclination_deg": req.start_inclination_deg,
        "raan_deg": req.start_raan_deg,
        "latitude": 0.0,
        "longitude": 0.0,
    }
    return result


@app.post("/plan")
def plan(req: PlanRequest):
    """Risk-ranked pool -> orienteering optimizer -> route + reasoning-ready breakdown."""
    result = _run_plan(req)
    explanation = _explain_plan(result)
    result["explanation"] = explanation
    if explanation is None and result.get("visited_count", 0) > 0:
        result["explanation_error"] = (
            "Mission briefing generation failed or was rate-limited. "
            "Route data above is valid; retry to get a narrated briefing."
        )
    return result


@app.post("/mission-cost")
def mission_cost(req: MissionCostRequest):
    """Custom Selection mode: forced-visit TSP over a user-specified debris set.

    Every ID in target_norad_ids *must* be visited -- the solver finds the
    optimal visit order and reports the total fuel cost.  Unlike /plan, there
    is no fuel budget cap: the answer is the fuel required, not whether it
    fits within a budget.

    Validation mirrors /plan's target_norad_id checks: 404 if an ID isn't in
    the current debris field, 422 if it's classified monitor_only.
    """
    scored = _get_scored_field()
    if not scored:
        raise HTTPException(status_code=502, detail="Debris field empty -- Celestrak fetch may have failed")

    targets: list[dict[str, Any]] = []
    for norad_id in req.target_norad_ids:
        obj = next((o for o in scored if o["norad_id"] == norad_id), None)
        if obj is None:
            raise HTTPException(
                status_code=404,
                detail=f"norad_id {norad_id} not found in current debris field",
            )
        if obj.get("removal_method") == METHOD_MONITOR_ONLY:
            raise HTTPException(
                status_code=422,
                detail=f"norad_id {norad_id} is classified monitor_only -- not a viable route target.",
            )
        targets.append(obj)

    result = solve_forced_route(
        targets,
        start_altitude_km=req.start_altitude_km,
        start_inclination_deg=req.start_inclination_deg,
        start_raan_deg=req.start_raan_deg,
    )

    # Hard solver errors (degenerate orbital elements, truly infeasible geometry)
    # are surfaced as HTTP 422 — these indicate a bad request, not a soft planning
    # outcome the user can act on by changing targets.
    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    result["depot"] = {
        "altitude_km": req.start_altitude_km,
        "inclination_deg": req.start_inclination_deg,
        "raan_deg": req.start_raan_deg,
        "latitude": 0.0,
        "longitude": 0.0,
    }

    # Fix 1: generate LLM mission briefing on valid routes, mirroring /plan.
    # _explain_plan() returns None when route_details is empty (no stops visited),
    # so it is always safe to call unconditionally here.
    explanation = _explain_plan(result)
    result["explanation"] = explanation
    if explanation is None and result.get("visited_count", 0) > 0:
        result["explanation_error"] = (
            "Mission briefing generation failed or was rate-limited. "
            "Route data above is valid; retry to get a narrated briefing."
        )

    return result


# ---------------------------------------------------------------------------
# /replan helpers
# ---------------------------------------------------------------------------

# groq==0.11.0 supports response_format={"type": "json_object"} only (no
# json_schema mode). The JSON schema is described in the system prompt instead.
_GROQ_TIMEOUT = 20.0
_ALLOWED_OVERRIDE_KEYS = {"fuel_budget_km_s", "weights", "removal_method_filter", "no_changes", "launch_site", "inclination_deg"}

def _build_parse_prompt(req: "PlanRequest") -> str:
    """Build the system prompt with current parameter values embedded so the
    model can resolve relative instructions like 'cut in half' or 'double it'."""
    base_weights = req.weights or DEFAULT_WEIGHTS
    current_site = req.launch_site or "(none — raw orbit)"
    site_keys = sorted(LAUNCH_SITES.keys())
    return (
        "You are a parameter-extraction assistant for an orbital debris removal mission planner. "
        "The mission currently has these parameter values:\n"
        f"  fuel_budget_km_s   = {req.fuel_budget_km_s}  (also called: fuel budget, delta-v budget, fuel limit)\n"
        f"  weights.proximity  = {base_weights.get('proximity', DEFAULT_WEIGHTS['proximity'])}  (also called: proximity weight, congestion weight)\n"
        f"  weights.lifetime   = {base_weights.get('lifetime',  DEFAULT_WEIGHTS['lifetime'])}  (also called: lifetime weight, drag weight)\n"
        f"  weights.size       = {base_weights.get('size',      DEFAULT_WEIGHTS['size'])}  (also called: size weight, object size weight)\n"
        f"  inclination_deg    = {req.inclination_deg!r}  (orbital inclination override in degrees; only relevant when using a launch site)\n"
        f"  removal_method_filter = {req.removal_method_filter!r}  (also called: capture method, hardware type; "
        f"valid values are {sorted(_VALID_REMOVAL_METHOD_FILTERS)} or null for no filter/mixed methods)\n"
        f"  launch_site = {current_site!r}  (the spacecraft's launch site; "
        f"valid keys are exactly: {site_keys})\n"
        "\n"
        "From the user's message, extract ONLY the parameters they want to change and output a single valid JSON object. "
        "The only keys you may emit are:\n"
        "  fuel_budget_km_s   -- positive float (km/s)\n"
        "  weights            -- object with keys 'proximity' (float 0-1) and/or 'lifetime' (float 0-1) and/or 'size' (float 0-1)\n"
        f"  removal_method_filter -- one of {sorted(_VALID_REMOVAL_METHOD_FILTERS)}, or null to clear an existing filter\n"
        f"  inclination_deg -- float in degrees, only if the user clearly requests an inclination change\n"
        f"  launch_site -- one of {site_keys} if the user clearly names a different launch site; "
        "omit entirely if the user does not mention a site change\n"
        "\n"
        "Rules:\n"
        "- Resolve relative instructions using the current values shown above "
        "(e.g. 'cut in half' -> divide the current value by 2; 'double it' -> multiply by 2; "
        "'reduce by 20%' -> multiply by 0.8).\n"
        "- Omit any key the user did not mention.\n"
        "- For launch_site: only emit it when the user clearly names one of the five listed sites. "
        "If the user names a real location that is NOT in the list, do NOT guess or invent a key — "
        "omit launch_site entirely.\n"
        "- If the message contains no recognisable parameter change at all, return exactly {\"no_changes\": true}.\n"
        "- Output ONLY the JSON object -- no prose, no markdown."
    )


def _groq_client() -> groq_module.Groq:
    """Construct a Groq client from the environment. GROQ_API_KEY is loaded
    from .env by load_dotenv() at module import time."""
    return groq_module.Groq(
        api_key=os.environ.get("GROQ_API_KEY"),
        timeout=_GROQ_TIMEOUT,
    )



def _explain_removal_method(
    removal_method: str,
    possible_methods: list[str],
    method_maturity: dict[str, str],
) -> tuple[str, str]:
    """Return (explanation, source) for a removal_method recommendation.

    Cached by removal_method alone: only 3 distinct values exist in
    removal_method.py, so the cache is bounded at 3 entries and generic
    enough to reuse across every object with the same method."""
    if removal_method in _REMOVAL_METHOD_EXPLANATION_CACHE:
        return _REMOVAL_METHOD_EXPLANATION_CACHE[removal_method]

    prompt = (
        "You are a technical writer for an orbital debris removal programme. "
        "Write exactly 1-2 plain-English sentences explaining why the following "
        "removal method is appropriate for objects of this type, referencing the "
        "method's real-world flight status where relevant.\n\n"
        "Ground your answer in these known facts from the programme's classification logic:\n"
        "- net_capture is flight-demonstrated (RemoveDEBRIS 2018-2019 deployed a net successfully).\n"
        "- robotic_arm is conceptual for uncooperative debris — no flown precedent exists "
        "(ClearSpace-1 targets a single cooperative adapter; ELSA-M requires a pre-installed "
        "docking plate; neither addresses tumbling uncooperative fragments).\n"
        "- monitor_only is not a capture technique — it means the object is tracked by the "
        "Space Surveillance Network but is too small for active removal in current missions.\n\n"
        f"Removal method label: {removal_method}\n"
        f"Possible technique(s): {possible_methods}\n"
        f"Maturity per technique: {method_maturity}\n\n"
        "Do NOT reference any specific object name, NORAD ID, or live orbital data. "
        "Output only the 1-2 sentence explanation — no JSON, no markdown, no preamble."
    )

    resp = _groq_client().chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    text = (resp.choices[0].message.content or "").strip()
    result: tuple[str, str] = (text, "llm")
    _REMOVAL_METHOD_EXPLANATION_CACHE[removal_method] = result
    return result



def _parse_overrides(user_text: str, req: "PlanRequest") -> dict[str, Any]:
    """Call llama-3.1-8b-instant in json_object mode to extract parameter overrides."""
    system_prompt = _build_parse_prompt(req)
    resp = _groq_client().chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_text},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    last_raw = resp.choices[0].message.content or ""
    logger.debug("[_parse_overrides] raw LLM response: %r", last_raw)
    raw = json.loads(last_raw)
    return {k: v for k, v in raw.items() if k in _ALLOWED_OVERRIDE_KEYS}


def _explain_diff(diff: dict[str, Any]) -> str:
    """Call openai/gpt-oss-120b with ONLY the diff dict to generate a
    2-3 sentence plain-language explanation. Raw route data is never passed."""
    prompt = (
        "You are a mission-briefing assistant for an orbital debris removal programme. "
        "The following JSON describes the difference between an old route plan and a new one "
        "after a parameter change. Write exactly 2-3 plain-English sentences summarising what "
        "changed and why it matters for the mission. Do not speculate beyond the diff numbers. "
        "Important: budget_used_delta is a difference of fuel_used_fraction values, which are "
        "on a 0-1 scale (e.g. -0.894 means fuel usage dropped by 89.4 percentage points, not "
        "0.894%). Express it as percentage points (multiply by 100) or describe it using the "
        "actual fuel_used_fraction values from old_plan/new_plan if they are present. "
        "If site_change is present in the diff, describe the launch-site change "
        "(old site -> new site) and the resulting inclination/RAAN shift as a SEPARATE sentence "
        "from any weight or parameter changes — do not merge the two into one sentence. "
        "Output only the explanation -- no JSON, no markdown.\n\n"
        + json.dumps(diff)
    )
    resp = _groq_client().chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return (resp.choices[0].message.content or "").strip()


def _explain_plan(route_result: dict[str, Any]) -> Optional[str]:
    """Call openai/gpt-oss-120b with route_details (+ skip context) to
    generate a 2-3 sentence plain-language mission briefing."""
    details = route_result.get("route_details", [])
    if not details:
        return None  # nothing visited -- the `warning` field already covers this case

    method_counts: dict[str, int] = {}
    for obj in details:
        m = obj.get("removal_method", "unclassified")
        method_counts[m] = method_counts.get(m, 0) + 1

    prompt = (
        "You are a mission-briefing assistant for an orbital debris removal programme. "
        "Write exactly 2-3 plain-English sentences briefing the operator on this planned route. "
        "Focus on: how many objects are targeted, the mix of removal methods needed, and total "
        "fuel/risk collected. If any objects were skipped, add a brief, high-level reason "
        "(cost-vs-risk tradeoff) -- do not speculate about specific objects that were skipped. "
        "Output only the briefing -- no JSON, no markdown.\n\n"
        + json.dumps({
            "visited_count": route_result.get("visited_count"),
            "removal_method_counts": method_counts,
            "total_fuel_cost_km_s": route_result.get("total_fuel_cost_km_s"),
            "fuel_budget_km_s": route_result.get("fuel_budget_km_s"),
            "fuel_used_fraction": route_result.get("fuel_used_fraction"),
            "total_risk_collected": route_result.get("total_risk_collected"),
            "skipped_count": route_result.get("skipped_count"),
        })
    )

    resp = _groq_client().chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    return (resp.choices[0].message.content or "").strip()


def _norad_ids_from_plan(plan_result: dict[str, Any]) -> set[int]:
    """Extract the set of NORAD IDs visited in a plan result.
    optimizer._label() formats non-depot nodes as 'NAME (norad_id)', so we
    parse the trailing integer from each route label. Depot label has no parens."""
    ids: set[int] = set()
    for label in plan_result.get("route", []):
        m = re.search(r"\((\d+)\)$", label)
        if m:
            ids.add(int(m.group(1)))
    return ids


@app.post("/replan")
def replan(req: ReplanRequest):
    """Parse user_request_text into parameter overrides, re-run the plan,
    diff old vs new, and return a plain-language explanation. Stateless."""

    # ------------------------------------------------------------------ #
    # Step 1 -- parse overrides from natural language via small LLM       #
    # ------------------------------------------------------------------ #
    try:
        parsed = _parse_overrides(req.user_request_text, req)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # ------------------------------------------------------------------ #
    # Step 2 -- if LLM found nothing, return original plan unchanged      #
    # ------------------------------------------------------------------ #
    if not parsed or parsed.get("no_changes"):
        original_plan = _run_plan(req)
        original_plan["explanation"] = _explain_plan(original_plan)
        return {
            "old_plan": original_plan,
            "new_plan": original_plan,
            "diff": {
                "added": [],
                "dropped": [],
                "fuel_delta_km_s": 0.0,
                "risk_delta": 0.0,
                "budget_used_delta": 0.0,
            },
            "explanation": (
                "No recognised parameter changes were found in your request. "
                "The plan is returned unchanged."
            ),
            "overrides_applied": {},
        }

    # ------------------------------------------------------------------ #
    # Step 3 -- validate overrides before touching the optimizer          #
    # ------------------------------------------------------------------ #
    overrides: dict[str, Any] = {}

    if "fuel_budget_km_s" in parsed:
        v = float(parsed["fuel_budget_km_s"])
        if v <= 0:
            raise HTTPException(status_code=422, detail="fuel_budget_km_s must be > 0")
        # optimizer.py converts the budget to an integer via
        # round(fuel_budget_km_s * 1000).  Any value below 0.0005 rounds to 0,
        # setting OR-Tools' Fuel dimension capacity to zero -- no arc can be
        # traversed, so the solver returns a valid non-None solution that visits
        # nothing.  0.001 gives 2× margin above the 0.0005 rounding cliff.
        if v < 0.001:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"fuel_budget_km_s must be >= 0.001 km/s (got {v}). "
                    "Values below this round to zero fuel capacity in the "
                    "optimizer, producing a silent empty route."
                ),
            )
        overrides["fuel_budget_km_s"] = v

    if "weights" in parsed:
        base_weights = req.weights or DEFAULT_WEIGHTS
        w_raw = parsed["weights"]
        has_p = "proximity" in w_raw
        has_l = "lifetime"  in w_raw
        has_s = "size"      in w_raw
        # C2: always resolve all three weights — previously only proximity+lifetime
        # were handled, leaving size at its DEFAULT value, which made the three
        # weights sum to 1.25 (0.45+0.30+0.25+0.25) and corrupted risk scoring.
        p = float(w_raw["proximity"]) if has_p else float(base_weights.get("proximity", DEFAULT_WEIGHTS["proximity"]))
        l = float(w_raw["lifetime"])  if has_l else float(base_weights.get("lifetime",  DEFAULT_WEIGHTS["lifetime"]))
        s = float(w_raw["size"])      if has_s else float(base_weights.get("size",      DEFAULT_WEIGHTS["size"]))
        if not (0.0 <= p <= 1.0) or not (0.0 <= l <= 1.0) or not (0.0 <= s <= 1.0):
            raise HTTPException(status_code=422, detail="Weight values must be in [0, 1]")
        total = p + l + s
        if abs(total - 1.0) > 1e-6:
            if total > 0:
                p, l, s = p / total, l / total, s / total  # normalize all three to sum=1
            else:
                # all-zero edge case: reset to defaults
                p, l, s = DEFAULT_WEIGHTS["proximity"], DEFAULT_WEIGHTS["lifetime"], DEFAULT_WEIGHTS["size"]
        overrides["weights"] = {"proximity": round(p, 6), "lifetime": round(l, 6), "size": round(s, 6)}

    if "removal_method_filter" in parsed:
        v = parsed["removal_method_filter"]
        if v is not None and v not in _VALID_REMOVAL_METHOD_FILTERS:
            raise HTTPException(
                status_code=422,
                detail=f"removal_method_filter must be one of {sorted(_VALID_REMOVAL_METHOD_FILTERS)} "
                       f"or null, got {v!r}",
            )
        overrides["removal_method_filter"] = v  # None is a valid, meaningful override (clears the filter)

    if "launch_site" in parsed:
        v = parsed["launch_site"]
        if v is None:
            # Explicit null from the LLM means "clear the site" — treat as
            # no site on the new request (raw fields will carry through from
            # model_dump since they're already resolved on req).
            overrides["launch_site"] = None
        elif v in LAUNCH_SITES:
            # Known key — valid site change.
            overrides["launch_site"] = v
            if "inclination_deg" in parsed:
                overrides["inclination_deg"] = parsed["inclination_deg"]
        else:
            # Unknown key: LLM hallucinated or partially matched a location
            # not in the five-site catalog.  Per design: silently ignore,
            # leave start_position unchanged.  Do NOT raise 422 — this is
            # unmatched user free text routed through the parser, not a
            # malformed direct API call.
            logger.warning(
                "[replan] launch_site from LLM %r is not in LAUNCH_SITES — ignoring",
                v,
            )

    # ------------------------------------------------------------------ #
    # Step 4 -- compute old plan (original params) and new plan (merged)  #
    # ------------------------------------------------------------------ #
    logger.debug("[replan] req.model_dump() before old_plan: %s", req.model_dump())
    old_plan = _run_plan(req)
    # old_plan intentionally has no "explanation" key.
    # Design rationale: old_plan is being discarded; narrating it would cost an
    # extra LLM call for a plan the user just asked to replace, with no downstream
    # consumer (the frontend renders new_plan's stats/briefing, not old_plan's).
    # The schema asymmetry is intentional -- old_plan.explanation is absent,
    # not None, by design.  See CHANGELOG "FIX #1 — old_plan explanation".

    new_req_data = req.model_dump()
    new_req_data.update(overrides)
    if "launch_site" in overrides:
        # Intentional site change from the LLM parser: null out the already-
        # resolved raw fields so the model_validator re-derives them from the
        # new site key.  Without this the validator's idempotency guard would
        # see the old populated values and skip re-resolution.
        new_req_data["start_altitude_km"]    = None
        new_req_data["start_inclination_deg"] = None
    # ReplanRequest has user_request_text; PlanRequest doesn't -- strip it
    new_req_data.pop("user_request_text", None)
    new_req = PlanRequest(**new_req_data)
    new_plan = _run_plan(new_req)

    # ------------------------------------------------------------------ #
    # Step 5 -- diff old vs new                                           #
    # ------------------------------------------------------------------ #
    old_ids = _norad_ids_from_plan(old_plan)
    new_ids = _norad_ids_from_plan(new_plan)

    diff: dict[str, Any] = {
        "added":   sorted(new_ids - old_ids),
        "dropped": sorted(old_ids - new_ids),
        "fuel_delta_km_s":    round(
            new_plan["total_fuel_cost_km_s"] - old_plan["total_fuel_cost_km_s"], 4
        ),
        "risk_delta":         round(
            new_plan["total_risk_collected"] - old_plan["total_risk_collected"], 4
        ),
        "budget_used_delta":  round(
            new_plan["fuel_used_fraction"] - old_plan["fuel_used_fraction"], 4
        ),
    }

    # If the launch site changed, add a site_change entry to the diff so
    # _explain_diff()'s LLM prompt can describe it as a separate sentence.
    old_site = req.launch_site
    new_site = new_req.launch_site
    if old_site != new_site:
        diff["site_change"] = {
            "old_site": old_site,
            "new_site": new_site,
            "old_inclination_deg": req.start_inclination_deg,
            "new_inclination_deg": new_req.start_inclination_deg,
            "old_raan_deg": req.start_raan_deg,
            "new_raan_deg": new_req.start_raan_deg,
        }

    # ------------------------------------------------------------------ #
    # Step 6 -- ask large LLM for plain-language explanation (diff only)  #
    # ------------------------------------------------------------------ #
    try:
        explanation = _explain_diff(diff)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # ------------------------------------------------------------------ #
    # Step 7 -- narrate new_plan only (not old_plan -- it's being         #
    # discarded, no value briefing a plan the user is replacing). Soft-  #
    # fails to None, same as /plan, rather than raising -- the diff      #
    # explanation above is the primary payload of /replan; a briefing    #
    # failure here shouldn't take down an otherwise-successful response. #
    # ------------------------------------------------------------------ #
    new_plan["explanation"] = _explain_plan(new_plan)
    if new_plan["explanation"] is None and new_plan.get("visited_count", 0) > 0:
        new_plan["explanation_error"] = (
            "Mission briefing generation failed or was rate-limited. "
            "Route data above is valid; retry to get a narrated briefing."
        )

    return {
        "old_plan":         old_plan,
        "new_plan":         new_plan,
        "diff":             diff,
        "explanation":      explanation,
        "overrides_applied": overrides,
    }


@app.get("/naive-route")
def naive_route(
    fuel_budget_km_s: float,
    start_altitude_km: Optional[float] = None,
    start_inclination_deg: Optional[float] = None,
    launch_site: Optional[str] = None,
    inclination_deg: Optional[float] = None,
    pool_size: int = DEFAULT_POOL_SIZE,
    start_raan_deg: float = 0.0,
    max_tle_age_days: float = 14.0,
):
    """Nearest-neighbor baseline for the naive-vs-AI comparison (Week 5 Day 35).
    Greedy: always hop to whatever's cheapest next, ignore risk entirely,
    stop once the next hop would blow the budget. This is the strawman the
    optimizer's smarter risk-vs-fuel tradeoff gets compared against."""
    # --- launch_site / raw-orbit resolution (mirrors resolve_launch_site) ---
    has_site = launch_site is not None
    has_alt  = start_altitude_km is not None
    has_incl = start_inclination_deg is not None

    if has_site and not (has_alt and has_incl):
        # Resolve via launch site; reject unknown keys immediately (not an LLM
        # caller, so we never silently drop bad input like /replan does).
        if launch_site not in LAUNCH_SITES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown launch site {launch_site!r}. "
                    f"Valid keys: {sorted(LAUNCH_SITES)}"
                ),
            )
        orbit = derive_start_orbit(
            launch_site,
            inclination=inclination_deg,
            altitude_km=start_altitude_km or 800,
        )
        start_altitude_km    = orbit["altitude_km"]
        start_inclination_deg = orbit["inclination_deg"]
        start_raan_deg        = orbit["raan_deg"]
    elif not has_site and not (has_alt and has_incl):
        raise HTTPException(
            status_code=422,
            detail=(
                "Either launch_site or both start_altitude_km and "
                "start_inclination_deg must be provided."
            ),
        )
    # --- end resolution ---

    scored = _get_scored_field()
    # Apply the same TLE-age filter as _run_plan() so the naive baseline
    # and the AI route operate on the same data quality window -- without
    # this, naive_route could silently use stale objects that /plan excluded,
    # making the comparison unfair.  Same reasoning as the earlier RAAN/depot
    # symmetry fix.
    scored = [o for o in scored if o.get("epoch_age_days", 0.0) <= max_tle_age_days]
    pool = select_candidate_pool(scored, pool_size=pool_size)

    from app.cost_matrix import build_cost_matrix

    depot = {"norad_id": -1, "name": "DEPOT (spacecraft start)", "altitude_km": start_altitude_km, "inclination_deg": start_inclination_deg, "raan_deg": start_raan_deg, "risk_score": 0.0}
    nodes = [depot] + pool
    matrix = build_cost_matrix(nodes)

    visited_idx: list[int] = []
    remaining = set(range(1, len(nodes)))
    current = 0
    fuel_used = 0.0
    elapsed_days = 0.0  # cumulative mission time, same convention as optimizer.py
    steps: list[dict[str, Any]] = []

    # Must be defined before the while loop — called inside it at each step.
    def _label(obj: dict) -> str:
        if obj["norad_id"] == -1:
            return obj["name"]
        return f"{obj['name']} ({obj['norad_id']})"

    while remaining:
        next_idx = min(remaining, key=lambda j: matrix[current][j])
        hop_cost = matrix[current][next_idx]
        if fuel_used + hop_cost > fuel_budget_km_s:
            break
        fuel_used += hop_cost
        steps.append({
            # H4: use _label() so the manifest shows "NAME (norad_id)" format,
            # matching the AI route manifest and enabling globe polyline resolution.
            "from": _label(nodes[current]),
            "to":   _label(nodes[next_idx]),
            "delta_v_km_s": round(hop_cost, 4),
            "arrival_time_days": round(elapsed_days, 4),
            # naive_route does not model RAAN drift -- the greedy walk uses static
            # fetch-time costs throughout, unlike optimizer.py's post-solve drift walk.
            "raan_drift_deg": 0.0,
        })
        # Advance elapsed time using the same heuristic constant optimizer.py uses,
        # so arrival_time_days is on the same scale across both routes.
        elapsed_days += hop_cost * TRANSFER_TIME_DAYS_PER_KM_S
        visited_idx.append(next_idx)
        remaining.discard(next_idx)
        current = next_idx

    visited_objects = [nodes[i] for i in visited_idx]

    # route_details in the same shape optimize_route() produces, so
    # _explain_plan() (which reads removal_method off route_details to
    # build its method-mix summary) works identically for both routes --
    # required for the naive-vs-AI comparison to actually be a fair one.
    route_details = [
        {
            "norad_id": o["norad_id"],
            "name": o["name"],
            "object_type": o.get("object_type", "unknown"),
            "removal_method": o.get("removal_method", "unclassified"),
            "possible_methods": o.get("possible_methods", []),
            "method_maturity": o.get("method_maturity", {}),
            "risk_score": round(o.get("risk_score", 0.0), 4),
        }
        for o in visited_objects
    ]

    depot_row = matrix[0][1:]  # list of raw km/s floats, one per pool node
    min_depot_hop_km_s: float = round(min(depot_row), 4) if depot_row else 0.0

    skipped_objects = [nodes[i] for i in range(1, len(nodes)) if i not in set(visited_idx)]

    result = {
        "route": [_label(o) for o in visited_objects],
        "route_details": route_details,
        "visited_count": len(visited_objects),
        "skipped_count": len(pool) - len(visited_objects),
        "skipped_names": [_label(o) for o in skipped_objects],
        "pool_size_used": len(pool),
        "total_fuel_cost_km_s": round(fuel_used, 4),
        "fuel_budget_km_s": fuel_budget_km_s,
        "fuel_used_fraction": round(fuel_used / fuel_budget_km_s, 4) if fuel_budget_km_s > 0 else 0.0,
        "total_risk_collected": round(sum(o.get("risk_score", 0.0) for o in visited_objects), 4),
        "step_breakdown": steps,
        "min_depot_hop_km_s": min_depot_hop_km_s,
        # naive_route is always single-vehicle, no OR-Tools net cap --
        # echo 1 for shape parity rather than None (avoids consumer null checks).
        "net_capacity_constrained": 1,
    }

    if result["visited_count"] == 0:
        result["warning"] = (
            f"No debris nodes were visited within the given constraints. "
            f"The cheapest depot hop on this pool is ~{min_depot_hop_km_s} km/s -- "
            f"try raising fuel_budget_km_s above that value."
        )

    explanation = _explain_plan(result)
    result["explanation"] = explanation
    if explanation is None and result.get("visited_count", 0) > 0:
        result["explanation_error"] = (
            "Mission briefing generation failed or was rate-limited. "
            "Route data above is valid; retry to get a narrated briefing."
        )

    # Echo depot so the frontend can draw the depot marker and the
    # depot->first-hop leg -- same shape and same convention as /plan's
    # result["depot"] block in _run_plan().  latitude/longitude default to
    # 0.0 (equatorial crossing): we only have orbital elements from the
    # query params, not a real ground-track position.
    result["depot"] = {
        "altitude_km": start_altitude_km,
        "inclination_deg": start_inclination_deg,
        "raan_deg": start_raan_deg,
        "latitude": 0.0,
        "longitude": 0.0,
    }
    return result