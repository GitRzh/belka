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
import hashlib
import json
import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import groq as groq_module
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

load_dotenv()

from app.tle_fetch import get_debris_field, get_cache_timestamp, CACHE_MAX_AGE_SECONDS, CACHE_FILE as _TLE_CACHE_FILE
from app.launch_sites import LAUNCH_SITES, derive_start_orbit
from app.risk_score import score_debris_field, DEFAULT_WEIGHTS
from app.cost_matrix import select_candidate_pool, DEFAULT_POOL_SIZE, DELTA_V_SCALE
from app.optimizer import optimize_route, solve_forced_route, compute_pareto_frontier, TRANSFER_TIME_DAYS_PER_KM_S, DRY_RUN_TIME_LIMIT_SECONDS
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

# In-memory cache for per-object removal-method REASONING (new expert system layer).
# Keyed by norad_id (int).  Populated on first call to GET /debris/{id}/removal-methods,
# then served from cache on all subsequent calls — never re-calls the LLM for the same id.
# Bounded by the number of distinct objects the user clicks on in a session; typical
# sessions visit a handful, so this never grows large.
_reasoning_cache: dict[int, dict] = {}

# M3: in-memory cache for the default scored + enriched field (no custom weights).
# Keyed by the TLE cache file's mtime string so it auto-invalidates whenever
# get_debris_field() writes a fresh fetch.  Custom-weights calls (from /plan or
# /replan with non-default weights) bypass this cache because their output
# depends on the caller-supplied weights dict and must not cross-contaminate.
_scored_field_cache: dict[str, list] = {}  # {"<mtime>": [enriched_objects]}

# Maximum day_offset allowed for launch_date on PlanRequest and the sweep window.
# Must match _TLE_AGING_DAYS so both paths use the same reliability cap.
_MAX_LAUNCH_DAY_OFFSET = 14.0


def _debris_epoch() -> datetime:
    """Single source of truth for the TLE reference epoch used by both the
    sweep's launch_date construction and _run_plan's drift computation.

    Returns the UTC datetime of the last TLE cache write (the mtime of
    CACHE_FILE).  Both callers anchor their day_offset arithmetic to this
    fixed point rather than wall-clock 'now', eliminating any midnight-
    boundary drift between a sweep and the /plan call that follows it.

    MUST be called only AFTER _get_scored_field() has been invoked in the
    same request, which guarantees CACHE_FILE exists (get_debris_field()
    writes it on first fetch).  Both sweep_launch_window and _run_plan
    call _get_scored_field() unconditionally at their top before touching
    this function — that ordering is structural, not incidental."""
    mtime = os.path.getmtime(_TLE_CACHE_FILE)
    return datetime.fromtimestamp(mtime, tz=timezone.utc)


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


class PreviewOrbitRequest(BaseModel):
    altitude_km: float = Field(..., gt=0, description="Circular orbit altitude, km")
    inclination_deg: float = Field(..., ge=0, le=180, description="Orbital inclination, degrees")
    raan_deg: float = Field(0.0, ge=0, lt=360, description="Right ascension of ascending node, degrees")
    time_iso: Optional[str] = Field(None, description="ISO-8601 UTC time for position calc; defaults to now")


@app.post("/preview-orbit")
def preview_orbit(req: PreviewOrbitRequest):
    """Convert circular-orbit elements (altitude, inclination, RAAN) to a
    ground-track latitude/longitude at the requested time (default: now).

    Uses a purely analytical propagation for a circular orbit:
      - The ascending node crosses the equator at longitude = RAAN - GMST.
      - The satellite's true anomaly advances at the mean motion from t=0
        (assumed at the ascending node, i.e. argument of latitude = 0 at t=0).
      - Sub-satellite latitude/longitude are derived from the unit position vector
        projected back through the inclination rotation.

    This is a first-order approximation suitable for "where roughly is the
    orbital plane right now" visualisation — not for precise conjunction analysis.
    """
    # Earth constants
    R_EARTH_KM = 6371.0
    MU_KM3_S2  = 398600.4418   # Earth gravitational parameter, km³/s²

    # Epoch: parse or use current UTC
    if req.time_iso:
        try:
            epoch = datetime.fromisoformat(req.time_iso.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid time_iso format: {req.time_iso!r}")
    else:
        epoch = datetime.now(timezone.utc)

    # Seconds since J2000.0 (2000-01-01T12:00:00 UTC)
    J2000 = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t_sec = (epoch - J2000).total_seconds()

    # Semi-major axis (km) and mean motion (rad/s) for circular orbit
    a_km      = R_EARTH_KM + req.altitude_km
    n_rad_s   = math.sqrt(MU_KM3_S2 / (a_km ** 3))

    # Greenwich Mean Sidereal Time (GMST) in radians.
    # IAU formula: θ_GMST = 280.46061837 + 360.98564736629 * T_days (degrees)
    t_days   = t_sec / 86400.0
    gmst_deg = (280.46061837 + 360.98564736629 * t_days) % 360.0
    gmst_rad = math.radians(gmst_deg)

    # RAAN in the inertial frame is given; the satellite starts at the ascending
    # node (argument of latitude = 0) at t=0 and advances at mean motion.
    raan_rad  = math.radians(req.raan_deg)
    incl_rad  = math.radians(req.inclination_deg)
    u_rad     = n_rad_s * t_sec  # argument of latitude at epoch (mod 2π)

    # Position unit vector in ECI (perifocal -> ECI rotation):
    # r_hat = (cos u · N̂ + sin u · P̂) where N̂ and P̂ are orbit-frame basis vectors
    # N̂ = (cos Ω, sin Ω, 0)  — node direction
    # P̂ = (−sin Ω·cos i, cos Ω·cos i, sin i) — 90° ahead in orbit plane
    cos_u   = math.cos(u_rad)
    sin_u   = math.sin(u_rad)
    cos_O   = math.cos(raan_rad)
    sin_O   = math.sin(raan_rad)
    cos_i   = math.cos(incl_rad)
    sin_i   = math.sin(incl_rad)

    x_eci = cos_u * cos_O - sin_u * sin_O * cos_i
    y_eci = cos_u * sin_O + sin_u * cos_O * cos_i
    z_eci = sin_u * sin_i

    # ECI → ECEF: rotate by -GMST around Z axis
    cos_gmst = math.cos(gmst_rad)
    sin_gmst = math.sin(gmst_rad)
    x_ecef =  x_eci * cos_gmst + y_eci * sin_gmst
    y_ecef = -x_eci * sin_gmst + y_eci * cos_gmst
    z_ecef =  z_eci

    # Latitude / longitude from ECEF unit vector
    lat_deg = math.degrees(math.asin(max(-1.0, min(1.0, z_ecef))))
    lon_deg = math.degrees(math.atan2(y_ecef, x_ecef))

    return {"lat": round(lat_deg, 6), "lon": round(lon_deg, 6)}


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
    max_wait_days: float = Field(0.0, ge=0, le=30, description="Maximum days to wait at each leg for a lower-cost RAAN alignment. 0.0 (default) disables the wait-window scan and reproduces legacy behaviour exactly.")
    min_saving_km_s: float = Field(0.0, ge=0, description="Minimum delta-v saving (km/s) a wait must achieve before it is recommended. 0.0 (default) recommends any wait that reduces cost at all.")
    removal_method_filter: Optional[str] = Field(None, description=f"Restrict the route to a single removal method -- one of {sorted(_VALID_REMOVAL_METHOD_FILTERS)}. No real ADR mission has flown mixed capture hardware (RemoveDEBRIS = net+harpoon only, ELSA-M = magnetic docking only), so this models 'one spacecraft, one hardware type'. Unset preserves the current mixed-method behavior.")
    target_norad_id: Optional[int] = Field(None, description="Force this object to be considered by the optimizer even if it wouldn't normally rank into the top pool_size by risk. The optimizer still decides whether to actually visit it (AddDisjunction still applies) -- this only guarantees consideration, not a visit. Rejected if the object is classified monitor_only (never a real target).")
    max_tle_age_days: float = Field(14.0, description="Exclude objects whose TLE epoch is older than this many days from route planning. Default 14.0 matches published TLE-accuracy research (~2 week reliable window). Raise to include older/less-trusted debris, lower to be stricter. Does not affect /debris-field or /debris/{norad_id}, which always show the full field with data_quality labels.")
    launch_date: Optional[str] = Field(
        None,
        description=(
            "ISO-8601 UTC date (YYYY-MM-DD) or datetime (YYYY-MM-DDTHH:MM:SSZ) of the planned "
            "mission start. When present, _run_plan applies raan_drift_deg() to start_raan_deg "
            "so the depot node reflects the mission's actual orbital plane at launch. "
            "day_offset is computed as (launch_date - TLE epoch) and is capped at "
            "14 days (the TLE reliability window). Absent = day_offset 0, zero behaviour change "
            "for any existing caller. Populated by clicking a point in the Launch Window Explorer."
        ),
    )

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
    user_request_text: str = Field(
        "",
        description=(
            "Plain-English override instructions, e.g. 'use only 1.5 km/s of fuel'. "
            "Required (non-empty) when applied_proposal is None. "
            "May be omitted or empty when applied_proposal is supplied — the proposal "
            "params are used directly and this field is ignored."
        ),
    )
    applied_proposal: Optional[dict] = Field(
        None,
        description=(
            "Pre-structured override dict from a validated constraint-resolution proposal "
            "(the 'params' dict from a /plan proposals entry, merged with its fix_type). "
            "When present, _parse_overrides() is skipped entirely — zero extra LLM calls. "
            "The same per-type validation as the free-text path is applied before the plan runs."
        ),
    )
    exclude_norad_ids: list[int] = Field(
        default_factory=list,
        description=(
            "NORAD IDs to exclude from the candidate pool for the NEW plan "
            "only (old_plan is unaffected). Intended for objects already "
            "collected before an anomaly — no verification against actual "
            "route history is performed; this is caller-asserted state, "
            "not system-tracked."
        ),
    )

    @model_validator(mode="after")
    def require_text_or_proposal(self) -> "ReplanRequest":
        """Ensure the request carries either free text or a pre-parsed proposal.
        An empty user_request_text is only valid when applied_proposal is provided."""
        if self.applied_proposal is None and not self.user_request_text.strip():
            raise ValueError(
                "user_request_text must be non-empty when applied_proposal is not provided."
            )
        return self


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
    max_wait_days: float = Field(0.0, ge=0, le=30, description="Maximum days to wait at each leg for a lower-cost RAAN alignment. 0.0 (default) disables the wait-window scan and reproduces legacy behaviour exactly.")
    min_saving_km_s: float = Field(0.0, ge=0, description="Minimum delta-v saving (km/s) a wait must achieve before it is recommended. 0.0 (default) recommends any wait that reduces cost at all.")

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


# Allowlist for alternatives returned by the reasoning LLM.  Any name outside
# this set is silently dropped — the LLM may hallucinate methods that don't
# exist in this system's classification logic.
_REASONING_ALT_ALLOWLIST = {"net_capture", "robotic_arm", "monitor_only"}


@app.get("/debris/{norad_id}/removal-methods")
def debris_removal_methods(norad_id: int):
    """Expert-system reasoning layer on top of the existing removal_method classification.

    Returns the LLM-generated reasoning for WHY a specific removal method was chosen
    for this object, grounded ONLY in available signals: BSTAR, altitude, inclination,
    risk_score, and the existing removal_method classification from removal_method.py.

    Caches by norad_id in _reasoning_cache for the process lifetime.
    Single engine: Groq openai/gpt-oss-20b (distinct from the openai/gpt-oss-120b
    model used by _explain_plan/_explain_diff for route briefings).
    If the call fails: returns reasoning_unavailable=True with no reasoning text (never 500).
    """
    # Step 1 — look up the object
    scored = _get_scored_field()
    obj = next((o for o in scored if o["norad_id"] == norad_id), None)
    if obj is None:
        raise HTTPException(
            status_code=404,
            detail=f"norad_id {norad_id} not found in current 700-1000km band field",
        )

    removal_method = obj.get("removal_method", "unknown")

    # Step 2 — serve from cache if already generated
    if norad_id in _reasoning_cache:
        return _reasoning_cache[norad_id]

    # Step 3 — build prompt using ONLY real available signals (no invented mass/material)
    bstar = obj.get("bstar", 0.0)
    altitude_km = obj.get("altitude_km")
    inclination_deg = obj.get("inclination_deg")
    risk_score = obj.get("risk_score")
    object_type = obj.get("object_type", "unknown")

    # Describe BSTAR in relative terms so the LLM uses only the signal,
    # not inventing mass numbers — the prompt explicitly forbids specific mass/material.
    bstar_abs = abs(bstar)
    if bstar_abs > 1e-4:
        bstar_desc = f"{bstar:.4e} (high — indicates a low-mass, high-drag object)"
    elif bstar_abs > 1e-5:
        bstar_desc = f"{bstar:.4e} (moderate drag)"
    else:
        bstar_desc = f"{bstar:.4e} (low — indicates a denser/larger object with less atmospheric drag)"

    prompt = (
        "You are an orbital debris removal expert system. "
        "Your task is to explain, in 2-4 plain-English sentences, WHY the selected removal "
        "method is appropriate for this specific debris object, using ONLY the orbital signals "
        "listed below. Do NOT state or infer a specific mass in kg, a specific material name "
        "(e.g. aluminium, titanium), or any physical property that is not derivable from the "
        "signals. You may make relative statements such as 'high BSTAR suggests a low-mass, "
        "high-drag object'. Also suggest 1-3 alternatives with a one-sentence reason each.\n\n"
        f"Object signals:\n"
        f"  BSTAR (drag term):    {bstar_desc}\n"
        f"  Altitude:             {altitude_km} km\n"
        f"  Inclination:          {inclination_deg}°\n"
        f"  Risk score:           {risk_score}\n"
        f"  Object type:          {object_type} ({'fragment with DEB in name' if object_type == 'fragment' else 'intact/parent object, no DEB in name'})\n"
        f"  Removal method chosen: {removal_method}\n\n"
        "Respond with ONLY a JSON object — no prose, no markdown — in this exact shape:\n"
        '{"reasoning": "<2-4 sentences>", '
        '"alternatives": [{"name": "<method>", "why": "<one sentence>"}]}\n'
        "The 'name' in each alternative must be one of: "
        "net_capture, robotic_arm, monitor_only."
    )

    # Step 4 — single Groq call (openai/gpt-oss-20b).
    # Intentionally distinct from openai/gpt-oss-120b used by _explain_plan/_explain_diff
    # for route briefings — logged with feature tag [removal-methods] for traceability.
    reasoning_text: Optional[str] = None
    raw_alternatives: list[dict] = []

    try:
        groq_resp = _groq_client().chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            timeout=20.0,
        )
        raw_text = (groq_resp.choices[0].message.content or "").strip()
        # Strip markdown fences if the model wraps in ```json ... ```
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
            raw_text = re.sub(r"\n?```$", "", raw_text)
        parsed = json.loads(raw_text)
        reasoning_text = parsed.get("reasoning")
        raw_alternatives = parsed.get("alternatives", [])
        logger.info(
            "[removal-methods] norad_id=%d reasoning served (model=openai/gpt-oss-20b)",
            norad_id,
        )
    except Exception as groq_err:
        logger.error(
            "[removal-methods] Groq failed for norad_id=%d: %s — returning reasoning_unavailable",
            norad_id,
            groq_err,
        )

    # Step 5 — filter alternatives against the fixed allowlist.
    # Drop any entry whose 'name' is not in _REASONING_ALT_ALLOWLIST so that
    # hallucinated method names (e.g. laser_ablation) never reach the client.
    alternatives = [
        a for a in raw_alternatives
        if isinstance(a, dict) and a.get("name") in _REASONING_ALT_ALLOWLIST
    ]

    # Step 6 — build response; never crash even if the LLM failed
    reasoning_unavailable = reasoning_text is None
    response: dict[str, Any] = {
        "norad_id": norad_id,
        "removal_method": removal_method,
        "reasoning": reasoning_text,
        "reasoning_unavailable": reasoning_unavailable,
        "alternatives": alternatives,
    }

    # Cache before returning — even on failure, so repeated clicks on a
    # broken-LLM session don't retry an already-failed call.
    _reasoning_cache[norad_id] = response
    return response


def _run_plan(
    req: PlanRequest,
    *,
    time_limit_seconds: Optional[int] = None,
    exclude_norad_ids: Optional[list[int]] = None,
) -> dict[str, Any]:
    """Execute the full plan pipeline for a PlanRequest and return the result dict.
    Shared by /plan and /replan so both endpoints stay in sync.

    time_limit_seconds: when set, overrides SOLVER_TIME_LIMIT_SECONDS for the
    optimize_route() call.  Used by _dry_run_plan() to pass DRY_RUN_TIME_LIMIT_SECONDS
    so feasibility checks complete in ~1s instead of the full 5s budget.

    exclude_norad_ids: when set explicitly by the caller, overrides any value
    that may be present as req.exclude_norad_ids (attribute on ReplanRequest).
    Callers that pass a ReplanRequest directly (e.g. tests) still work unchanged
    via the getattr fallback; _execute_overrides passes this explicitly so that
    new_plan is filtered even though new_req is a bare PlanRequest (no field).

    launch_date (on req): when present, computes day_offset = (launch_date - TLE epoch)
    and applies raan_drift_deg() to start_raan_deg so the depot reflects the actual
    orbital plane at launch.  Absent → day_offset 0, zero behaviour change.
    day_offset is capped at _MAX_LAUNCH_DAY_OFFSET (14 days) to prevent bypassing the
    TLE reliability window by setting a far-future launch_date directly on PlanRequest.
    """
    # STRUCTURAL ordering: _get_scored_field MUST come before _debris_epoch() so the
    # TLE cache file exists when _debris_epoch() reads its mtime.
    scored = _get_scored_field(weights=req.weights)
    if not scored:
        raise HTTPException(status_code=502, detail="Debris field empty -- Celestrak fetch may have failed")

    # --- launch_date → RAAN drift (Guardrails 1, 2, 3) ---
    # Guardrail 1: absent launch_date → day_offset 0, start_raan_deg unchanged.
    # Guardrail 2: epoch anchor is _debris_epoch() (TLE cache mtime), not wall-clock
    #              'now' — same anchor the sweep used, so clicking a point and then
    #              generating a plan always solves the same day_offset.
    # Guardrail 3: cap day_offset at _MAX_LAUNCH_DAY_OFFSET; reject values beyond it.
    effective_raan_deg = req.start_raan_deg
    if req.launch_date is not None:
        try:
            # Accept both date-only ("2025-07-15") and full datetime ("…T12:00:00Z").
            ld_str = req.launch_date.strip()
            if "T" in ld_str:
                launch_dt = datetime.fromisoformat(ld_str.replace("Z", "+00:00"))
            else:
                launch_dt = datetime.fromisoformat(ld_str).replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"launch_date {req.launch_date!r} is not a valid ISO-8601 date or datetime.",
            )
        epoch_dt = _debris_epoch()
        day_offset = (launch_dt - epoch_dt).total_seconds() / 86400.0
        # Guardrail 3: cap / reject beyond TLE reliability window.
        if day_offset > _MAX_LAUNCH_DAY_OFFSET:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"launch_date {req.launch_date!r} is {day_offset:.1f} days after the TLE epoch "
                    f"({epoch_dt.date().isoformat()}), which exceeds the {_MAX_LAUNCH_DAY_OFFSET:.0f}-day "
                    "TLE reliability window. Use a launch_date within 14 days of the TLE epoch."
                ),
            )
        if day_offset < 0.0:
            day_offset = 0.0  # past launch_date → treat as now, no drift
        from app.delta_v import raan_drift_deg
        drift = raan_drift_deg(req.start_altitude_km, req.start_inclination_deg, day_offset)
        effective_raan_deg = req.start_raan_deg + drift

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

    effective_exclude = exclude_norad_ids if exclude_norad_ids is not None else getattr(req, "exclude_norad_ids", None)
    if effective_exclude:
        exclude_set = set(effective_exclude)
        scored = [o for o in scored if o["norad_id"] not in exclude_set]

    # Filtering happens on `scored` (before pool selection) rather than
    # after, so select_candidate_pool's top-pool_size ranking is computed
    # over the already-restricted set -- otherwise a filter could silently
    # shrink the effective pool below pool_size even when plenty of
    # matching objects exist further down the risk ranking.
    depot_for_pool = {
        "altitude_km": req.start_altitude_km,
        "inclination_deg": req.start_inclination_deg,
        "raan_deg": effective_raan_deg,
    }
    pool = select_candidate_pool(
        scored,
        pool_size=req.pool_size,
        depot=depot_for_pool,
        fuel_budget_km_s=req.fuel_budget_km_s,
    )

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

    opt_kwargs: dict[str, Any] = {}
    if time_limit_seconds is not None:
        opt_kwargs["time_limit_seconds"] = time_limit_seconds
    result = optimize_route(
        pool,
        fuel_budget_km_s=req.fuel_budget_km_s,
        start_altitude_km=req.start_altitude_km,
        start_inclination_deg=req.start_inclination_deg,
        start_raan_deg=effective_raan_deg,
        nets_carried=req.nets_carried,
        max_wait_days=req.max_wait_days,
        min_saving_km_s=req.min_saving_km_s,
        **opt_kwargs,
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
        if min_hop is None:
            result["warning"] = (
                "No debris nodes were visited: no objects are reachable within "
                "the given fuel budget. Try raising fuel_budget_km_s."
            )
        else:
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
        "raan_deg": effective_raan_deg,
        "latitude": 0.0,
        "longitude": 0.0,
    }
    return result


def _dry_run_plan(req: PlanRequest) -> dict[str, Any]:
    """Run the plan pipeline with a short time limit to confirm feasibility only.

    Used exclusively by the dry-run validation layer inside _propose_fixes() to
    check whether a proposed fix would yield visited_count > 0.  The 1 s limit
    (DRY_RUN_TIME_LIMIT_SECONDS) is enough for OR-Tools to find any feasible
    solution — it does not need to find the optimal route, just confirm one exists.
    Never raises: returns the result dict (possibly visited_count == 0) or
    {"visited_count": 0, "dry_run_error": str(exc)} on unexpected failure."""
    try:
        return _run_plan(req, time_limit_seconds=DRY_RUN_TIME_LIMIT_SECONDS)
    except Exception as exc:
        logger.warning("[dry_run_plan] unexpected error: %s", exc)
        return {"visited_count": 0, "dry_run_error": str(exc)}


# ---------------------------------------------------------------------------
# Agentic Constraint Resolution helpers
# ---------------------------------------------------------------------------

# Allowlist of fix_types the LLM is permitted to propose.  Any value outside
# this set is silently dropped at Layer 2 so hallucinated types never reach
# the client.
_VALID_FIX_TYPES = {"budget_increase", "pool_size_increase", "altitude_expand", "method_filter_change"}

# Required params key per fix_type (Layer 2 structural check).
_FIX_TYPE_PARAMS_KEY: dict[str, str] = {
    "budget_increase":      "new_budget",
    "pool_size_increase":   "new_pool_size",
    "altitude_expand":      "altitude_km",
    "method_filter_change": "removal_method",
}

# Mapping from fix_type-specific param key → canonical PlanRequest / override key.
# Defined here — near _FIX_TYPE_PARAMS_KEY — so both _build_dry_run_req and
# _execute_overrides share the same data structure rather than two independent
# if/elif chains that can drift.
_PROPOSAL_PARAM_TO_OVERRIDE: dict[str, str] = {
    # fix_type "budget_increase"      → params{"new_budget": <float>}
    "new_budget":     "fuel_budget_km_s",
    # fix_type "pool_size_increase"   → params{"new_pool_size": <int>}
    "new_pool_size":  "pool_size",
    # fix_type "altitude_expand"      → params{"altitude_km": <float>}
    "altitude_km":    "start_altitude_km",
    # fix_type "method_filter_change" → params{"removal_method": <str|null>}
    "removal_method": "removal_method_filter",
}

# Valid method values for method_filter_change (Layer 3 bounds check).
_VALID_PROPOSAL_METHODS = {"net_capture", "robotic_arm_or_net_capture"}

# Timeout budget for the entire _propose_fixes call (LLM + validation).
_PROPOSE_FIXES_TIMEOUT = 3.0  # seconds


def _validate_proposals(raw_proposals: list[Any]) -> list[dict[str, Any]]:
    """Layer 2 + Layer 3 validation.  Drop (and log) any proposal that fails
    either structural or bounds checks.  Never raises."""
    _required_fields = {"proposal", "reason", "fix_type", "params", "estimated_impact"}
    validated: list[dict[str, Any]] = []

    for raw in raw_proposals:
        # Layer 2 — structural
        if not isinstance(raw, dict):
            logger.warning("[propose_fixes] rejected non-dict proposal: %r", raw)
            continue
        missing = _required_fields - raw.keys()
        if missing:
            logger.warning("[propose_fixes] rejected proposal missing fields %s: %r", missing, raw)
            continue
        fix_type = raw.get("fix_type")
        if fix_type not in _VALID_FIX_TYPES:
            logger.warning("[propose_fixes] rejected proposal with unknown fix_type %r: %r", fix_type, raw)
            continue
        params = raw.get("params")
        if not isinstance(params, dict):
            logger.warning("[propose_fixes] rejected proposal with non-dict params: %r", raw)
            continue
        required_param_key = _FIX_TYPE_PARAMS_KEY[fix_type]
        if required_param_key not in params:
            logger.warning(
                "[propose_fixes] rejected proposal: fix_type=%r requires params.%s, not found: %r",
                fix_type, required_param_key, raw,
            )
            continue

        # Layer 3 — bounds per fix_type
        try:
            if fix_type == "budget_increase":
                v = float(params["new_budget"])
                if not (0.5 <= v <= 50):
                    raise ValueError(f"new_budget {v} outside [0.5, 50]")
            elif fix_type == "pool_size_increase":
                v = int(params["new_pool_size"])
                if not (5 <= v <= 300):
                    raise ValueError(f"new_pool_size {v} outside [5, 300]")
            elif fix_type == "altitude_expand":
                v = float(params["altitude_km"])
                if not (500 <= v <= 2000):
                    raise ValueError(f"altitude_km {v} outside [500, 2000]")
            elif fix_type == "method_filter_change":
                m = params["removal_method"]
                if m not in _VALID_PROPOSAL_METHODS:
                    raise ValueError(f"removal_method {m!r} not in {_VALID_PROPOSAL_METHODS}")
        except (ValueError, TypeError) as bounds_err:
            logger.warning("[propose_fixes] rejected out-of-bounds proposal (%s): %r", bounds_err, raw)
            continue

        validated.append(raw)

    return validated


def _build_dry_run_req(req: PlanRequest, proposal: dict[str, Any]) -> Optional[PlanRequest]:
    """Translate a validated proposal's params into a PlanRequest for dry-run.

    Uses ``_PROPOSAL_PARAM_TO_OVERRIDE`` to map fix-type-specific param keys to
    canonical PlanRequest field names — the same mapping ``_execute_overrides``
    uses via ``_translate_proposal_params``, so the two paths stay in sync.

    Returns None for fix_types that cannot meaningfully be expressed as a
    PlanRequest override (currently none — all four map cleanly), or if the
    constructed request would be invalid.  Callers must treat None as
    "skip dry-run for this proposal"."""
    fix_type = proposal.get("fix_type")
    params   = proposal.get("params", {})
    if fix_type not in _VALID_FIX_TYPES:
        return None
    try:
        base = req.model_dump()
        base.pop("user_request_text", None)
        base.pop("applied_proposal", None)

        # Translate each proposal param key to the canonical PlanRequest field.
        # Type coercions (float/int) match the original per-type branches exactly.
        param_key = _FIX_TYPE_PARAMS_KEY[fix_type]
        override_key = _PROPOSAL_PARAM_TO_OVERRIDE[param_key]
        raw_val = params[param_key]
        if fix_type == "pool_size_increase":
            base[override_key] = int(raw_val)
        elif fix_type in ("budget_increase", "altitude_expand"):
            base[override_key] = float(raw_val)
        else:  # method_filter_change — str or None, pass through as-is
            base[override_key] = raw_val

        return PlanRequest(**base)
    except Exception as exc:
        logger.warning("[dry_run] could not build PlanRequest for %r: %s", fix_type, exc)
        return None


def _propose_fixes(route_result: dict[str, Any], req: PlanRequest) -> list[dict[str, Any]]:
    """Call llama-3.1-8b-instant to propose 2-3 concrete fixes for a failed plan
    (visited_count == 0).

    Pipeline:
      1. Ask the LLM for candidate proposals (with a short timeout).
      2. Validate them (Layer 2 structural + Layer 3 bounds).
      3. Dry-run each validated proposal concurrently with a 1 s OR-Tools limit
         to confirm the fix actually yields visited_count > 0 before showing it
         to the user.  Proposals whose dry-run visits 0 objects are dropped.

    Returns only proposals confirmed feasible by the dry-run, or [] on any
    failure (timeout, bad JSON, Groq error, zero surviving proposals).
    Never raises — the route result is always returned regardless."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    result_holder: list[list[dict[str, Any]]] = [[]]

    def _run() -> None:
        min_hop   = route_result.get("min_depot_hop_km_s", "unknown")
        pool_size = route_result.get("pool_size_used", req.pool_size)
        budget    = req.fuel_budget_km_s
        weights   = req.weights or DEFAULT_WEIGHTS
        rmf       = req.removal_method_filter

        prompt = (
            "You are a constraint-resolution assistant for an orbital debris removal mission planner. "
            "A route-planning run just failed: the optimizer visited ZERO debris objects because the "
            "mission constraints are too tight for any feasible hop.\n\n"
            "Failed run metadata:\n"
            f"  fuel_budget_km_s          = {budget}  (total delta-v budget)\n"
            f"  cheapest_depot_hop_km_s   = {min_hop}  (minimum delta-v cost to reach any object)\n"
            f"  pool_size                 = {pool_size}  (number of candidate objects considered)\n"
            f"  weights.proximity         = {weights.get('proximity', DEFAULT_WEIGHTS['proximity'])}\n"
            f"  weights.lifetime          = {weights.get('lifetime',  DEFAULT_WEIGHTS['lifetime'])}\n"
            f"  weights.size              = {weights.get('size',      DEFAULT_WEIGHTS['size'])}\n"
            f"  removal_method_filter     = {rmf!r}  (null means no filter / mixed methods)\n\n"
            "Propose 2-3 concrete fixes the operator can apply to make the route feasible. "
            "Each fix must address the root cause shown in the metadata above.\n\n"
            "ALLOWED fix_type values (use EXACTLY one of these four strings, nothing else):\n"
            '  "budget_increase"      — raise fuel_budget_km_s so it exceeds cheapest_depot_hop_km_s\n'
            '  "pool_size_increase"   — enlarge the candidate pool so cheaper objects become reachable\n'
            '  "altitude_expand"      — reposition the spacecraft/depot launch altitude (altitude_km) to\n'
            "                           reduce the first-hop delta-v cost; this does NOT add new debris\n"
            "                           objects to the pool — it moves the depot closer to existing ones.\n"
            '                           reason must describe reducing first-hop cost, NOT pool size change.\n'
            '  "method_filter_change" — relax or change the removal_method_filter to open more targets\n\n'
            "NUMERIC BOUNDS (your proposed values must stay within these ranges):\n"
            "  budget_increase:      0.5 <= new_budget <= 50        (km/s)\n"
            "  pool_size_increase:   5 <= new_pool_size <= 300       (objects)\n"
            "  altitude_expand:      500 <= altitude_km <= 2000      (km)\n"
            "  method_filter_change: removal_method must be exactly one of: "
            '"net_capture", "robotic_arm_or_net_capture"\n\n'
            "DO NOT invent fix_types not in the list above (e.g. do NOT use "
            '"inclination_change", "weight_adjust", "debris_selection", or any other string). '
            "DO NOT invent removal_method values not in the allowed list above "
            '(e.g. do NOT use "laser_ablation", "harpoon_capture", "ion_beam", "electrodynamic_tether"). '
            "DO NOT propose values outside the numeric bounds above (e.g. do NOT set new_budget=200).\n"
            'For altitude_expand, the reason MUST describe repositioning the spacecraft/depot altitude to '
            "lower the first-hop transit cost — do NOT mention pool size, number of objects, or debris "
            "coverage area changing.\n\n"
            "Respond with ONLY a JSON object (no prose, no markdown) in this exact shape:\n"
            '{"proposals": [\n'
            '  {\n'
            '    "proposal": "<one sentence describing the fix>",\n'
            '    "reason": "<one sentence explaining why this unblocks the route>",\n'
            '    "fix_type": "<one of the four allowed fix_type strings>",\n'
            '    "params": {"<key>": <value>},\n'
            '    "estimated_impact": "<one sentence on expected improvement>"\n'
            '  }\n'
            "]}"
        )

        try:
            resp = _groq_client().chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
                timeout=_PROPOSE_FIXES_TIMEOUT,
            )
            raw_text = resp.choices[0].message.content or ""
            logger.debug("[propose_fixes] raw LLM response: %r", raw_text)
            parsed = json.loads(raw_text)
            raw_proposals = parsed.get("proposals", [])
            if not isinstance(raw_proposals, list):
                logger.warning("[propose_fixes] 'proposals' is not a list: %r", raw_proposals)
                result_holder[0] = []
                return

            validated = _validate_proposals(raw_proposals)
            if not validated:
                result_holder[0] = []
                return

            # ------------------------------------------------------------------
            # Dry-run validation: run all candidates concurrently with a 1 s
            # time limit each.  Total wall time ≈ max(individual dry-run) not
            # sum — typically ~1 s for the slowest candidate.
            # ------------------------------------------------------------------
            dry_run_reqs: list[Optional[PlanRequest]] = [
                _build_dry_run_req(req, p) for p in validated
            ]

            confirmed: list[dict[str, Any]] = []

            def _run_dry(idx: int) -> tuple[int, int]:
                """Returns (proposal_index, visited_count)."""
                dr = dry_run_reqs[idx]
                if dr is None:
                    return idx, 0
                result = _dry_run_plan(dr)
                return idx, result.get("visited_count", 0)

            with ThreadPoolExecutor(max_workers=len(validated)) as pool:
                futures = {pool.submit(_run_dry, i): i for i in range(len(validated))}
                for future in as_completed(futures):
                    try:
                        idx, visited = future.result()
                        if visited > 0:
                            confirmed.append(validated[idx])
                        else:
                            logger.debug(
                                "[propose_fixes] dry-run: fix_type=%r visited=0 — dropped",
                                validated[idx].get("fix_type"),
                            )
                    except Exception as exc:
                        logger.warning("[propose_fixes] dry-run future error: %s", exc)

            # Restore original proposal order (as_completed is unordered).
            order = {id(p): i for i, p in enumerate(validated)}
            confirmed.sort(key=lambda p: order.get(id(p), 999))

            result_holder[0] = confirmed

        except Exception as exc:
            logger.warning("[propose_fixes] failed (%s: %s) — returning empty proposals", type(exc).__name__, exc)
            result_holder[0] = []

    # Run the LLM call + dry-run block in a daemon thread.
    # Timeout budget: LLM (3 s) + dry-run (1 s per candidate, concurrent) + margin.
    # Max_workers caps the thread count so we don't spawn unbounded threads on
    # a large proposal list.
    _TOTAL_TIMEOUT = _PROPOSE_FIXES_TIMEOUT + DRY_RUN_TIME_LIMIT_SECONDS + 2.0
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=_TOTAL_TIMEOUT)
    if t.is_alive():
        logger.warning("[propose_fixes] thread still running after %.1fs — returning empty proposals", _TOTAL_TIMEOUT)
        return []
    return result_holder[0]


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
    if result.get("visited_count") == 0:
        result["proposals"] = _propose_fixes(result, req)
    return result


# ---------------------------------------------------------------------------
# /compare — Trade-off Plan Comparator
# ---------------------------------------------------------------------------

_COMPARE_PRESETS: list[dict[str, Any]] = [
    {"label": "Fuel-Conservative", "weights": {"proximity": 0.70, "lifetime": 0.15, "size": 0.15}},
    {"label": "Balanced",          "weights": DEFAULT_WEIGHTS},                   # {"proximity":0.45,"lifetime":0.30,"size":0.25}
    {"label": "Risk-Aggressive",   "weights": {"proximity": 0.15, "lifetime": 0.45, "size": 0.40}},
]

# Cache: keyed by sha256 of (sorted preset weight tuples + serialised request params).
# Caches only the comparison_narration text; optimizer runs are never cached here.
_compare_narration_cache: dict[str, str] = {}


def _compare_cache_key(req: "PlanRequest") -> str:
    """Stable hash of the 3 fixed preset weight dicts + the incoming request params."""
    preset_part = json.dumps(
        [p["weights"] for p in _COMPARE_PRESETS], sort_keys=True
    )
    # Exclude fields not relevant to plan output (e.g. user_request_text on replan).
    req_part = json.dumps({
        "launch_site":           req.launch_site,
        "inclination_deg":       req.inclination_deg,
        "start_altitude_km":     req.start_altitude_km,
        "start_inclination_deg": req.start_inclination_deg,
        "start_raan_deg":        req.start_raan_deg,
        "fuel_budget_km_s":      req.fuel_budget_km_s,
        "pool_size":             req.pool_size,
        "nets_carried":          req.nets_carried,
        "max_wait_days":         req.max_wait_days,
        "min_saving_km_s":       req.min_saving_km_s,
        "removal_method_filter": req.removal_method_filter,
        "max_tle_age_days":      req.max_tle_age_days,
    }, sort_keys=True)
    raw = preset_part + "|" + req_part
    return hashlib.sha256(raw.encode()).hexdigest()


def _explain_comparison(presets_results: list[dict[str, Any]]) -> Optional[str]:
    """Single LLM call (openai/gpt-oss-120b) comparing all 3 preset results.

    Called only after all 3 concurrent optimizer runs have finished.
    Returns None on failure so callers degrade gracefully."""
    summaries = [
        {
            "label":                 pr["label"],
            "weights":               pr["weights"],
            "total_fuel_cost_km_s":  pr["total_fuel_cost_km_s"],
            "total_risk_collected":  pr["total_risk_collected"],
            "visited_count":         pr["visited_count"],
        }
        for pr in presets_results
    ]
    prompt = (
        "You are a mission-planning analyst for an orbital debris removal programme. "
        "Three route plans were generated using different risk-scoring weight profiles. "
        "The weights change which debris objects are ranked into the candidate pool -- "
        "they do NOT directly optimise for fuel. Any fuel difference between presets is "
        "a side-effect of which objects were selected, not the optimizer targeting fuel. "
        "Write exactly 2-3 plain-English sentences comparing the three plans: highlight "
        "the trade-off between fuel cost and risk collected, and explain which preset "
        "is most suitable for different operator priorities. "
        "Output only the comparison -- no JSON, no markdown, no preamble.\n\n"
        + json.dumps(summaries)
    )
    try:
        resp = _groq_client().chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.error("[compare] LLM call failed: %s", exc)
        return None


@app.post("/compare")
def compare(req: PlanRequest):
    """Run the 3 fixed weight presets concurrently and return a side-by-side comparison.

    Body: same shape as PlanRequest (weights field is ignored -- always runs the 3
    fixed presets internally).  The 3 optimize_route() calls run in parallel via a
    ThreadPoolExecutor so the total wall-clock time is ~5s, not ~15s.

    Response shape:
      {
        "presets": [
          {"label": ..., "weights": ..., "total_fuel_cost_km_s": ...,
           "total_risk_collected": ..., "visited_count": ..., "route_details": [...]},
          ...
        ],
        "comparison_narration": "..."
      }
    """
    cache_key = _compare_cache_key(req)

    def _run_one_preset(preset: dict[str, Any]) -> dict[str, Any]:
        preset_req = req.model_copy(update={"weights": preset["weights"]})
        result = _run_plan(preset_req)
        return {
            "label":                preset["label"],
            "weights":              preset["weights"],
            "total_fuel_cost_km_s": result.get("total_fuel_cost_km_s", 0.0),
            "total_risk_collected": result.get("total_risk_collected", 0.0),
            "visited_count":        result.get("visited_count", 0),
            "route_details":        result.get("route_details", []),
        }

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_run_one_preset, p) for p in _COMPARE_PRESETS]
        presets_results = [f.result() for f in futures]

    # Serve narration from cache if available; otherwise call LLM once.
    if cache_key in _compare_narration_cache:
        narration = _compare_narration_cache[cache_key]
    else:
        narration = _explain_comparison(presets_results)
        if narration:
            _compare_narration_cache[cache_key] = narration

    return {
        "presets": presets_results,
        "comparison_narration": narration,
    }


# ---------------------------------------------------------------------------
# Feature 4: Launch-Window Pareto Explorer
# ---------------------------------------------------------------------------

class SweepLaunchWindowRequest(BaseModel):
    """Request body for POST /sweep-launch-window.

    Launch-site vs raw-orbit path mirrors PlanRequest exactly (same validator).
    forced_target_ids present → single_axis sweep via solve_forced_route().
    forced_target_ids absent  → pareto_frontier sweep via _run_plan() path.
    """
    # --- start position (same two paths as PlanRequest) ---
    launch_site: Optional[str] = Field(None, description="Launch site key. Mutually exclusive with raw orbit fields.")
    inclination_deg: Optional[float] = Field(None, description="Inclination override for launch_site mode.")
    start_altitude_km: Optional[float] = Field(None, description="Orbit altitude km. Required unless launch_site used.")
    start_inclination_deg: Optional[float] = Field(None, description="Orbit inclination deg. Required unless launch_site used.")
    start_raan_deg: float = Field(0.0, description="Spacecraft RAAN deg. Defaults to 0.0.")
    # --- required ---
    fuel_budget_km_s: float = Field(..., gt=0, description="Delta-v budget passed to each per-date solve.")
    # --- sweep config ---
    window_days: int = Field(14, ge=1, le=14, description="Length of the sweep window in days (1–14, capped at TLE reliability window).")
    weights: Optional[dict[str, float]] = Field(None, description="Risk-score weight overrides; same semantics as PlanRequest.")
    forced_target_ids: Optional[list[int]] = Field(None, description="If present, activates single_axis mode: each date solves via solve_forced_route() over this fixed target list. Absent = pareto_frontier mode via standard optimizer.")
    # --- carry-through params ---
    pool_size: int = Field(DEFAULT_POOL_SIZE, gt=0)
    nets_carried: int = Field(1, ge=1)
    max_wait_days: float = Field(0.0, ge=0, le=30)
    min_saving_km_s: float = Field(0.0, ge=0)
    removal_method_filter: Optional[str] = Field(None)
    max_tle_age_days: float = Field(14.0)

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


# Per-sweep narration cache: keyed by sha256(start_position + weights + window_days + forced_target_ids).
_sweep_narration_cache: dict[str, str] = {}


def _sweep_cache_key(req: SweepLaunchWindowRequest) -> str:
    """Stable sha256 hash of the fields that determine a unique sweep result."""
    payload = json.dumps({
        "start_altitude_km":     req.start_altitude_km,
        "start_inclination_deg": req.start_inclination_deg,
        "start_raan_deg":        req.start_raan_deg,
        "launch_site":           req.launch_site,
        "inclination_deg":       req.inclination_deg,
        "fuel_budget_km_s":      req.fuel_budget_km_s,
        "weights":               req.weights,
        "window_days":           req.window_days,
        "forced_target_ids":     sorted(req.forced_target_ids) if req.forced_target_ids else None,
        "pool_size":             req.pool_size,
        "nets_carried":          req.nets_carried,
        "max_wait_days":         req.max_wait_days,
        "min_saving_km_s":       req.min_saving_km_s,
        "removal_method_filter": req.removal_method_filter,
        "max_tle_age_days":      req.max_tle_age_days,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _worst_data_quality(route_details: list[dict]) -> str:
    """Return the worst data_quality label among the visited debris in a result.
    Ranking: stale > aging > fresh. Falls back to 'unknown' if no details."""
    rank = {"stale": 2, "aging": 1, "fresh": 0, "unknown": -1}
    worst = "fresh"
    for d in route_details:
        q = d.get("data_quality", "unknown")
        if rank.get(q, -1) > rank.get(worst, 0):
            worst = q
    if not route_details:
        return "unknown"
    return worst


def _explain_sweep(
    window: list[dict[str, Any]],
    sweep_mode: str,
    lowest_fuel_date: dict[str, Any],
) -> Optional[str]:
    """Single LLM narration call over the full sweep result set.

    Prompt references only computed numbers (fuel cost, risk, frontier flags).
    In pareto_frontier mode lowest_fuel_date is explicitly framed as one
    reference point, not 'the' recommendation.
    Returns None on failure so callers degrade gracefully."""
    # Build a compact summary: only include fields relevant to the narration.
    summary_rows = [
        {
            "day_offset":           r["day_offset"],
            "launch_date":          r["launch_date"],
            "total_fuel_cost_km_s": r.get("total_fuel_cost_km_s"),
            "total_risk_collected": r.get("total_risk_collected"),
            "is_pareto_optimal":    r.get("is_pareto_optimal"),
            "visited_count":        r.get("visited_count"),
        }
        for r in window
        if "error" not in r
    ]

    if sweep_mode == "pareto_frontier":
        mode_context = (
            "This is a two-axis Pareto sweep: both fuel cost and risk collected vary by date "
            "because the optimizer selects different debris objects on different days. "
            f"The lowest-fuel date is day_offset={lowest_fuel_date['day_offset']} "
            f"({lowest_fuel_date['launch_date']}), but this is only one reference point on "
            "the frontier — a different date may collect more risk at higher fuel cost, which "
            "is a valid trade-off depending on operator priorities. "
            "Do NOT imply that the lowest-fuel date is 'the best' or 'recommended'."
        )
    else:
        mode_context = (
            "This is a single-axis sweep: the target list is fixed (Custom Selection mode), "
            "so risk collected is constant across all dates. Only fuel cost varies. "
            f"The lowest-fuel date is day_offset={lowest_fuel_date['day_offset']} "
            f"({lowest_fuel_date['launch_date']}), which is the unambiguous optimum since "
            "risk cannot be improved by a different date."
        )

    prompt = (
        "You are a mission-planning analyst for an orbital debris removal programme. "
        "A launch-window sweep was run over a 14-day window, solving an independent route "
        "for each candidate launch date using J2 RAAN drift to project the spacecraft's "
        "orbital plane forward. Each date's fuel cost and risk collected reflect the "
        "deterministic route the optimizer found for that specific starting RAAN. "
        + mode_context + " "
        "Write exactly 2-3 plain-English sentences summarising the sweep results: "
        "describe the range of fuel costs, highlight which dates are Pareto-optimal "
        "(or the best date in single-axis mode), and note any visible trend. "
        "Output only the narration — no JSON, no markdown, no preamble.\n\n"
        + json.dumps(summary_rows)
    )
    try:
        resp = _groq_client().chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.error("[sweep] LLM narration failed: %s", exc)
        return None


@app.post("/sweep-launch-window")
def sweep_launch_window(req: SweepLaunchWindowRequest):
    """Launch-Window Pareto Explorer — Feature 4.

    Sweeps window_days candidate launch dates, solving an independent route for
    each via J2 RAAN drift projection.  Returns a Pareto frontier (two-axis,
    fuel vs risk) when forced_target_ids is absent, or a single-axis best-date
    result when a fixed target list is present.

    Response shape: see spec (sweep_mode, window[], lowest_fuel_date, narration, echo).
    """
    from app.delta_v import raan_drift_deg
    from app.optimizer import _build_depot_node

    # STRUCTURAL: _get_scored_field MUST come before _debris_epoch() to ensure
    # the TLE cache file exists when _debris_epoch() reads its mtime.
    scored_all = _get_scored_field(weights=req.weights)
    if not scored_all:
        raise HTTPException(status_code=502, detail="Debris field empty -- Celestrak fetch may have failed")

    # Validate forced_target_ids early (before spawning threads).
    forced_targets: Optional[list[dict]] = None
    if req.forced_target_ids:
        id_to_obj: dict[int, dict] = {o["norad_id"]: o for o in scored_all}
        missing = [i for i in req.forced_target_ids if i not in id_to_obj]
        if missing:
            raise HTTPException(
                status_code=404,
                detail=f"forced_target_ids not found in current debris field: {missing}",
            )
        forced_targets = [id_to_obj[i] for i in req.forced_target_ids]

    # Validate removal_method_filter if present.
    if req.removal_method_filter is not None and req.removal_method_filter not in _VALID_REMOVAL_METHOD_FILTERS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"removal_method_filter must be one of {sorted(_VALID_REMOVAL_METHOD_FILTERS)} "
                f"or omitted, got {req.removal_method_filter!r}."
            ),
        )

    # Single frozen epoch for the entire sweep — called once here, passed into
    # every per-date worker.  A mid-sweep Celestrak refresh would otherwise give
    # different points on the same chart different anchors, corrupting comparisons.
    epoch_dt = _debris_epoch()

    def _day_to_launch_date(day_offset: float) -> str:
        """Convert day_offset (float) to ISO-8601 string anchored to epoch_dt.
        Whole-number offsets return date-only strings; fractional return full datetime."""
        dt = epoch_dt + timedelta(days=day_offset)
        if day_offset == int(day_offset):
            return dt.date().isoformat()
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _solve_one_date(day_offset: float) -> dict[str, Any]:
        """Run a single date solve and return a window-entry dict."""
        drift = raan_drift_deg(req.start_altitude_km, req.start_inclination_deg, day_offset)
        shifted_raan = req.start_raan_deg + drift
        launch_date_str = _day_to_launch_date(day_offset)

        if forced_targets is not None:
            # Custom Selection branch: solve_forced_route, NOT _run_plan.
            result = solve_forced_route(
                targets=forced_targets,
                start_altitude_km=req.start_altitude_km,
                start_inclination_deg=req.start_inclination_deg,
                start_raan_deg=shifted_raan,
                fuel_budget_km_s=req.fuel_budget_km_s,
                max_wait_days=req.max_wait_days,
                min_saving_km_s=req.min_saving_km_s,
            )
        else:
            # Standard optimizer path: build a PlanRequest for this date's RAAN.
            date_req = PlanRequest(
                start_altitude_km=req.start_altitude_km,
                start_inclination_deg=req.start_inclination_deg,
                start_raan_deg=shifted_raan,
                fuel_budget_km_s=req.fuel_budget_km_s,
                pool_size=req.pool_size,
                weights=req.weights,
                nets_carried=req.nets_carried,
                max_wait_days=req.max_wait_days,
                min_saving_km_s=req.min_saving_km_s,
                removal_method_filter=req.removal_method_filter,
                max_tle_age_days=req.max_tle_age_days,
                # launch_date intentionally NOT passed here: the sweep already
                # applied the drift via shifted_raan directly.  Passing launch_date
                # into _run_plan would double-apply the drift.
            )
            try:
                result = _run_plan(date_req)
            except HTTPException as exc:
                return {
                    "day_offset": day_offset,
                    "launch_date": launch_date_str,
                    "error": str(exc.detail),
                }

        if "error" in result:
            return {
                "day_offset": day_offset,
                "launch_date": launch_date_str,
                "error": result["error"],
            }

        route_details = result.get("route_details", [])
        return {
            "day_offset":            day_offset,
            "launch_date":           launch_date_str,
            "total_fuel_cost_km_s":  result.get("total_fuel_cost_km_s", 0.0),
            "total_risk_collected":  result.get("total_risk_collected", 0.0),
            "visited_count":         result.get("visited_count", 0),
            "data_quality":          _worst_data_quality(route_details),
        }

    # --- Coarse sweep: day_offsets 0.0 .. window_days, one per day ---
    coarse_offsets = [float(d) for d in range(req.window_days + 1)]

    with ThreadPoolExecutor(max_workers=min(len(coarse_offsets), 8)) as pool_exec:
        futures = [pool_exec.submit(_solve_one_date, off) for off in coarse_offsets]
        coarse_results: list[dict[str, Any]] = [f.result() for f in futures]

    # --- Refine pass: ±0.5 around local fuel-cost minima ---
    # A coarse result is a local minimum if it is valid (no "error") and its
    # total_fuel_cost_km_s is strictly less than both its neighbours.
    refine_offsets: list[float] = []
    for i, r in enumerate(coarse_results):
        if "error" in r:
            continue
        fuel = r["total_fuel_cost_km_s"]
        prev_fuel = coarse_results[i - 1]["total_fuel_cost_km_s"] if i > 0 and "error" not in coarse_results[i - 1] else float("inf")
        next_fuel = coarse_results[i + 1]["total_fuel_cost_km_s"] if i < len(coarse_results) - 1 and "error" not in coarse_results[i + 1] else float("inf")
        if fuel < prev_fuel and fuel < next_fuel:
            off = r["day_offset"]
            if off - 0.5 >= 0.0:
                refine_offsets.append(off - 0.5)
            if off + 0.5 <= float(req.window_days):
                refine_offsets.append(off + 0.5)

    refined_results: list[dict[str, Any]] = []
    if refine_offsets:
        with ThreadPoolExecutor(max_workers=min(len(refine_offsets), 8)) as pool_exec:
            futures = [pool_exec.submit(_solve_one_date, off) for off in refine_offsets]
            refined_results = [f.result() for f in futures]

    # Merge coarse + refined into a single window, sorted by day_offset.
    window_raw = coarse_results + refined_results
    window_raw.sort(key=lambda r: r["day_offset"])

    # --- Frontier filter ---
    forced_flag = req.forced_target_ids is not None
    annotated_window, sweep_mode = compute_pareto_frontier(window_raw, forced=forced_flag)

    # --- lowest_fuel_date: lowest fuel among valid results (tie → lower day_offset) ---
    valid_window = [r for r in annotated_window if "error" not in r]
    lowest_fuel_entry = min(
        valid_window,
        key=lambda r: (r["total_fuel_cost_km_s"], r["day_offset"]),
        default=None,
    )
    lowest_fuel_date = (
        {"day_offset": lowest_fuel_entry["day_offset"], "launch_date": lowest_fuel_entry["launch_date"]}
        if lowest_fuel_entry is not None
        else None
    )

    # --- LLM narration (single call, cached) ---
    cache_key = _sweep_cache_key(req)
    if cache_key in _sweep_narration_cache:
        narration = _sweep_narration_cache[cache_key]
    else:
        narration = _explain_sweep(annotated_window, sweep_mode, lowest_fuel_date or {})
        if narration:
            _sweep_narration_cache[cache_key] = narration

    # --- Build echo ---
    echo_start: dict[str, Any] = {
        "start_altitude_km":     req.start_altitude_km,
        "start_inclination_deg": req.start_inclination_deg,
        "start_raan_deg":        req.start_raan_deg,
    }
    if req.launch_site:
        echo_start["launch_site"] = req.launch_site
    if req.inclination_deg is not None:
        echo_start["inclination_deg"] = req.inclination_deg

    return {
        "sweep_mode":       sweep_mode,
        "window":           annotated_window,
        "lowest_fuel_date": lowest_fuel_date,
        "narration":        narration,
        "echo": {
            "start_position":    echo_start,
            "weights":           req.weights,
            "window_days":       req.window_days,
            "forced_target_ids": req.forced_target_ids,
        },
    }


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
        max_wait_days=req.max_wait_days,
        min_saving_km_s=req.min_saving_km_s,
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
    """Call llama-3.1-8b-instant in json_object mode to extract parameter overrides.

    Retries once on malformed JSON (model occasionally emits a broken fragment
    on the first token; a second call with the same prompt almost always fixes it).
    Raises ValueError on two consecutive failures so the caller can surface a 502."""
    system_prompt = _build_parse_prompt(req)
    client = _groq_client()
    last_raw = ""
    for attempt in range(2):
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_text},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        last_raw = resp.choices[0].message.content or ""
        logger.debug("[_parse_overrides] attempt=%d raw LLM response: %r", attempt, last_raw)
        try:
            raw = json.loads(last_raw)
            return {k: v for k, v in raw.items() if k in _ALLOWED_OVERRIDE_KEYS}
        except json.JSONDecodeError:
            if attempt == 0:
                logger.warning("[_parse_overrides] malformed JSON on attempt 0 — retrying")
                continue
    raise ValueError(f"malformed JSON after retry: {last_raw!r}")


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

    data_quality_counts: dict[str, int] = {}
    for obj in details:
        q = obj.get("data_quality", "unknown")
        data_quality_counts[q] = data_quality_counts.get(q, 0) + 1

    prompt = (
        "You are a mission-briefing assistant for an orbital debris removal programme. "
        "Write exactly 2-3 plain-English sentences briefing the operator on this planned route. "
        "Focus on: how many objects are targeted, the mix of removal methods needed, and total "
        "fuel/risk collected. If any objects were skipped, add a brief, high-level reason "
        "(cost-vs-risk tradeoff) -- do not speculate about specific objects that were skipped. "
        "If data_quality_counts contains aging or stale objects, use hedged language for those "
        "objects (e.g. 'likely', 'estimated') rather than stating figures flatly; if all objects "
        "are fresh, use the current confident tone unchanged. "
        "Output only the briefing -- no JSON, no markdown.\n\n"
        + json.dumps({
            "visited_count": route_result.get("visited_count"),
            "removal_method_counts": method_counts,
            "data_quality_counts": data_quality_counts,
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


def _translate_proposal_params(raw: dict) -> dict:
    """If *raw* contains a ``fix_type`` key (i.e. it arrived as a real proposal
    shape from the frontend), translate its fix-type-specific param key to the
    canonical override key that ``_execute_overrides`` understands.

    Example::

        {"fix_type": "budget_increase", "new_budget": 7.5}
        → {"fuel_budget_km_s": 7.5}

    The mapping is driven by ``_PROPOSAL_PARAM_TO_OVERRIDE`` — the **same**
    dict that powers ``_build_dry_run_req``'s translation — so both paths stay
    in sync by construction.

    Raises nothing.  Keys that aren't in the mapping are passed through
    unchanged (so canonical keys like ``fuel_budget_km_s`` already present in
    *raw* survive un-touched, preserving backwards compatibility with any caller
    that already pre-translates).
    """
    if "fix_type" not in raw:
        # Nothing to translate — already in canonical form (free-text path).
        return raw

    translated: dict[str, Any] = {}
    for k, v in raw.items():
        if k == "fix_type":
            continue  # consumed; not a PlanRequest field
        canonical = _PROPOSAL_PARAM_TO_OVERRIDE.get(k)
        if canonical is not None:
            translated[canonical] = v
        else:
            translated[k] = v  # pass through unknown / already-canonical keys
    return translated


def _execute_overrides(req: PlanRequest, raw_overrides: dict) -> dict:
    """Steps 3–7 of the replan pipeline: validate raw_overrides, compute old/new
    plans, diff, explain, and return the assembled response dict.

    Called by /replan in two ways:
      1. Free-text path  — raw_overrides came from _parse_overrides().
      2. Proposal path   — raw_overrides is req.applied_proposal (pre-structured
                           params from a validated proposal, bypassing LLM parse).

    Both paths go through the exact same per-type validation so there is no
    separate weaker path for the proposal shortcut.

    Proposal-shape dicts (containing a ``fix_type`` key) are translated to
    canonical override keys by ``_translate_proposal_params`` before validation
    so that e.g. ``{"fix_type": "budget_increase", "new_budget": 7.5}`` is
    handled identically to the already-translated ``{"fuel_budget_km_s": 7.5}``
    that the free-text path emits."""

    # ------------------------------------------------------------------ #
    # Step 2b -- if raw_overrides is in proposal shape, translate it     #
    # ------------------------------------------------------------------ #
    raw_overrides = _translate_proposal_params(raw_overrides)

    # ------------------------------------------------------------------ #
    # Step 3 -- validate overrides before touching the optimizer          #
    # ------------------------------------------------------------------ #
    overrides: dict[str, Any] = {}

    if "fuel_budget_km_s" in raw_overrides:
        v = float(raw_overrides["fuel_budget_km_s"])
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

    if "weights" in raw_overrides:
        base_weights = req.weights or DEFAULT_WEIGHTS
        w_raw = raw_overrides["weights"]
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

    if "removal_method_filter" in raw_overrides:
        v = raw_overrides["removal_method_filter"]
        if v is not None and v not in _VALID_REMOVAL_METHOD_FILTERS:
            raise HTTPException(
                status_code=422,
                detail=f"removal_method_filter must be one of {sorted(_VALID_REMOVAL_METHOD_FILTERS)} "
                       f"or null, got {v!r}",
            )
        overrides["removal_method_filter"] = v  # None is a valid, meaningful override (clears the filter)

    if "start_inclination_deg" in raw_overrides:
        # When inclination is supplied, altitude must also be present — a full
        # orbit position requires both.  Inclination-only is caller error.
        if "start_altitude_km" not in raw_overrides:
            raise HTTPException(
                status_code=422,
                detail="start_altitude_km and start_inclination_deg must be provided together.",
            )
        alt = float(raw_overrides["start_altitude_km"])
        incl = float(raw_overrides["start_inclination_deg"])
        if alt <= 0:
            raise HTTPException(status_code=422, detail="start_altitude_km must be > 0")
        overrides["start_altitude_km"] = alt
        overrides["start_inclination_deg"] = incl
        # start_raan_deg is optional — falls back to existing field default (0.0)
        # via new_req_data if not supplied.
        if "start_raan_deg" in raw_overrides:
            overrides["start_raan_deg"] = float(raw_overrides["start_raan_deg"])
    elif "start_altitude_km" in raw_overrides:
        # Altitude-only override (e.g. altitude_expand fix type): valid; no
        # inclination required because the existing inclination is kept via
        # new_req_data.  Validate the value itself.
        alt = float(raw_overrides["start_altitude_km"])
        if alt <= 0:
            raise HTTPException(status_code=422, detail="start_altitude_km must be > 0")
        overrides["start_altitude_km"] = alt

    if "launch_site" in raw_overrides:
        v = raw_overrides["launch_site"]
        if v is None:
            # Explicit null means "clear the site" — raw fields carry through
            # from model_dump since they're already resolved on req.
            overrides["launch_site"] = None
        elif v in LAUNCH_SITES:
            # Known key — valid site change.
            overrides["launch_site"] = v
            if "inclination_deg" in raw_overrides:
                overrides["inclination_deg"] = raw_overrides["inclination_deg"]
        else:
            # Unknown key: silently ignore, leave start_position unchanged.
            # Applies to the free-text path (LLM hallucination); the proposal
            # path never emits launch_site, so this branch is unreachable there.
            logger.warning(
                "[replan] launch_site %r is not in LAUNCH_SITES — ignoring",
                v,
            )

    # ------------------------------------------------------------------ #
    # Step 4 -- compute old plan (original params) and new plan (merged)  #
    # ------------------------------------------------------------------ #
    logger.debug("[replan/_execute_overrides] req.model_dump() before old_plan: %s", req.model_dump())
    old_plan_req = req.model_copy(update={"exclude_norad_ids": []})
    old_plan = _run_plan(old_plan_req)
    # old_plan intentionally has no "explanation" key.
    # Design rationale: old_plan is being discarded; narrating it would cost an
    # extra LLM call for a plan the user just asked to replace, with no downstream
    # consumer (the frontend renders new_plan's stats/briefing, not old_plan's).
    # The schema asymmetry is intentional -- old_plan.explanation is absent,
    # not None, by design.  See CHANGELOG "FIX #1 — old_plan explanation".

    new_req_data = req.model_dump()
    new_req_data.update(overrides)
    if "launch_site" in overrides:
        # Intentional site change: null out the already-resolved raw fields so
        # the model_validator re-derives them from the new site key.  Without
        # this the validator's idempotency guard would see the old populated
        # values and skip re-resolution.
        new_req_data["start_altitude_km"]    = None
        new_req_data["start_inclination_deg"] = None
    # ReplanRequest has user_request_text / applied_proposal / exclude_norad_ids;
    # PlanRequest doesn't — strip them before constructing new_req.
    new_req_data.pop("user_request_text", None)
    new_req_data.pop("applied_proposal", None)
    new_req_data.pop("exclude_norad_ids", None)
    new_req = PlanRequest(**new_req_data)
    new_plan = _run_plan(new_req, exclude_norad_ids=req.exclude_norad_ids)

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


@app.post("/replan")
def replan(req: ReplanRequest):
    """Parse user_request_text into parameter overrides, re-run the plan,
    diff old vs new, and return a plain-language explanation. Stateless.

    Two entry paths:
      • Free-text (req.applied_proposal is None): natural-language request is
        parsed into raw_overrides by the small LLM, then passed to
        _execute_overrides().
      • Proposal shortcut (req.applied_proposal is not None): raw_overrides
        come directly from the validated proposal params — _parse_overrides()
        is never called, so this path costs zero extra LLM calls."""

    # ------------------------------------------------------------------ #
    # Proposal shortcut — skip LLM parse entirely                        #
    # ------------------------------------------------------------------ #
    if req.applied_proposal is not None:
        return _execute_overrides(req, req.applied_proposal)

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

    return _execute_overrides(req, parsed)


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
    # Build the depot dict early so we can pass it into select_candidate_pool
    # for reachability-aware pool selection -- same depot that the cost matrix
    # and greedy walk will use below, ensuring consistency.
    depot = {"norad_id": -1, "name": "DEPOT (spacecraft start)", "altitude_km": start_altitude_km, "inclination_deg": start_inclination_deg, "raan_deg": start_raan_deg, "risk_score": 0.0}

    from app.cost_matrix import build_cost_matrix

    pool = select_candidate_pool(
        scored,
        pool_size=pool_size,
        depot=depot,
        fuel_budget_km_s=fuel_budget_km_s,
    )

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
            # Shape parity with optimizer.py step dicts; drift wait is not modelled here.
            "recommended_wait_days": 0,
            "fuel_saved_km_s": 0.0,
            "data_quality": nodes[next_idx].get("data_quality", "unknown"),
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
            "data_quality": o.get("data_quality", "unknown"),
        }
        for o in visited_objects
    ]

    depot_row = matrix[0][1:]  # list of raw km/s floats, one per pool node
    # Decision 1: when pool is empty there is no reachable object at all --
    # reporting 0.0 would read as "any hop is free", which is misleading.
    # Use None to signal "no reachable object in pool".
    min_depot_hop_km_s: float | None = round(min(depot_row), 4) if depot_row else None

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
        if min_depot_hop_km_s is None:
            result["warning"] = (
                "No debris nodes were visited: no objects are reachable within "
                "the given fuel budget. Try raising fuel_budget_km_s."
            )
        else:
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


# ---------------------------------------------------------------------------
# Leg explanation cache and endpoint (Feature 2 — Decision Provenance Inspector)
# ---------------------------------------------------------------------------

# In-memory cache keyed by (from_norad_id, to_norad_id) tuple.
# -1 is used as the depot sentinel (matches _build_depot_node in optimizer.py).
# Bounded by (number of visited nodes)^2 per session; in practice a handful of
# entries per session.  Never invalidated: orbital costs are deterministic for a
# given debris field snapshot.
_leg_explanation_cache: dict[tuple[int, int], dict] = {}


@app.get("/leg/{from_norad_id}/{to_norad_id}/explanation")
def leg_explanation(from_norad_id: int, to_norad_id: int,
                    delta_v_km_s: float = 0.0,
                    fuel_saved_km_s: float = 0.0,
                    recommended_wait_days: int = 0,
                    raan_drift_deg: float = 0.0,
                    arrival_time_days: float = 0.0):
    """Decision Provenance Inspector — why does this transfer leg cost what it does?

    Returns a short LLM-generated paragraph explaining the delta-v cost and any
    J2 passive wait recommendation for the leg from *from_norad_id* to *to_norad_id*.
    Leg metrics are supplied as query parameters so the frontend can pass the
    already-computed step_breakdown values without a second server-side solve.

    Caches server-side by (from_norad_id, to_norad_id) pair.
    Uses openai/gpt-oss-20b — same model/cost class as the per-object reasoning
    endpoint, intentionally distinct from the 120b model used for route briefings.
    Never returns 500: on LLM failure, explanation_unavailable=True is set.

    Both endpoint objects are looked up in the debris field; from_norad_id may be
    -1 for the depot sentinel (no object data available for depot).
    """
    cache_key = (from_norad_id, to_norad_id)
    if cache_key in _leg_explanation_cache:
        return _leg_explanation_cache[cache_key]

    scored = _get_scored_field()

    # Look up both endpoints.  Depot (norad_id == -1) has no debris record.
    from_obj = next((o for o in scored if o["norad_id"] == from_norad_id), None)
    to_obj   = next((o for o in scored if o["norad_id"] == to_norad_id),   None)

    if to_obj is None:
        raise HTTPException(
            status_code=404,
            detail=f"to_norad_id {to_norad_id} not found in current 700-1000km band field",
        )

    # Build human-readable endpoint descriptions for the prompt.
    def _obj_desc(obj: Optional[dict], label: str) -> str:
        if obj is None:
            return f"{label}: depot/spacecraft start (no orbital debris record)"
        return (
            f"{label}: {obj.get('name', 'unknown')} (NORAD {obj['norad_id']})\n"
            f"  altitude={obj.get('altitude_km')} km, "
            f"inclination={obj.get('inclination_deg')}°, "
            f"RAAN={obj.get('raan_deg', 0.0):.2f}°\n"
            f"  risk_score={obj.get('risk_score')}, "
            f"data_quality={obj.get('data_quality', 'unknown')}, "
            f"object_type={obj.get('object_type', 'unknown')}"
        )

    from_desc = _obj_desc(from_obj, "FROM")
    to_desc   = _obj_desc(to_obj,   "TO")

    wait_line = ""
    if recommended_wait_days > 0 and fuel_saved_km_s > 0:
        wait_line = (
            f"\n  J2 passive wait: {recommended_wait_days} day(s) recommended, "
            f"saving {fuel_saved_km_s} km/s by allowing nodal precession to reduce "
            f"the RAAN difference before the transfer burn."
        )

    raan_line = ""
    if abs(raan_drift_deg) > 0.01:
        raan_line = f"\n  RAAN drift over elapsed mission time: {raan_drift_deg:.4f}°"

    prompt = (
        "You are a mission-analysis assistant for an orbital debris removal programme. "
        "Write a single plain-English paragraph (3-5 sentences) explaining WHY this "
        "orbital transfer leg costs what it does, grounded ONLY in the orbital signals "
        "below. Use relative terms only — do NOT state specific object masses, materials, "
        "or any physical property not derivable from the signals. Reference the J2 "
        "passive nodal drift wait if recommended_wait_days > 0. Reference data_quality "
        "of the destination if it is 'aging' or 'stale', noting that trajectory "
        "uncertainty may affect the real cost. Do not speculate beyond the provided data. "
        "Output only the paragraph — no JSON, no markdown, no bullet points.\n\n"
        f"Leg metrics:\n"
        f"  delta_v_km_s           = {delta_v_km_s} km/s\n"
        f"  recommended_wait_days  = {recommended_wait_days} days\n"
        f"  fuel_saved_km_s        = {fuel_saved_km_s} km/s{wait_line}\n"
        f"  raan_drift_deg         = {raan_drift_deg:.4f}°{raan_line}\n"
        f"  arrival_time_days      = {arrival_time_days:.4f} days into mission\n\n"
        f"Endpoint objects:\n{from_desc}\n{to_desc}"
    )

    explanation_text: Optional[str] = None
    try:
        groq_resp = _groq_client().chat.completions.create(
            model="openai/gpt-oss-20b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            timeout=20.0,
        )
        explanation_text = (groq_resp.choices[0].message.content or "").strip()
        logger.info(
            "[leg-explanation] (%d->%d) served (model=openai/gpt-oss-20b)",
            from_norad_id, to_norad_id,
        )
    except Exception as groq_err:
        logger.error(
            "[leg-explanation] Groq failed for (%d->%d): %s — returning explanation_unavailable",
            from_norad_id, to_norad_id, groq_err,
        )

    # Build response — always succeeds even if LLM failed
    def _endpoint_summary(obj: Optional[dict], is_depot: bool) -> dict:
        if is_depot or obj is None:
            return {"norad_id": from_norad_id, "name": "Depot (spacecraft start)", "is_depot": True}
        return {
            "norad_id":        obj["norad_id"],
            "name":            obj.get("name"),
            "data_quality":    obj.get("data_quality", "unknown"),
            "epoch_age_days":  obj.get("epoch_age_days"),
            "risk_score":      obj.get("risk_score"),
            "is_depot":        False,
        }

    response: dict = {
        "from_norad_id":          from_norad_id,
        "to_norad_id":            to_norad_id,
        "from_obj":               _endpoint_summary(from_obj, from_norad_id == -1),
        "to_obj":                 _endpoint_summary(to_obj,   False),
        "delta_v_km_s":           delta_v_km_s,
        "fuel_saved_km_s":        fuel_saved_km_s,
        "recommended_wait_days":  recommended_wait_days,
        "raan_drift_deg":         raan_drift_deg,
        "arrival_time_days":      arrival_time_days,
        "explanation":            explanation_text,
        "explanation_unavailable": explanation_text is None,
    }

    _leg_explanation_cache[cache_key] = response
    return response
