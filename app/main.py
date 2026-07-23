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
from typing import Any, Optional

import groq as groq_module
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

from app.tle_fetch import get_debris_field
from app.risk_score import score_debris_field, DEFAULT_WEIGHTS
from app.cost_matrix import select_candidate_pool, DEFAULT_POOL_SIZE
from app.optimizer import optimize_route, RISK_PENALTY_SCALE

logger = logging.getLogger(__name__)

app = FastAPI(title="Orbital-Clean API")


def _get_scored_field(force_refresh: bool = False, weights: Optional[dict[str, float]] = None) -> list[dict[str, Any]]:
    """Shared by /debris-field, /debris/{norad_id}, and /plan so there's one
    place that does fetch+score -- avoids the three endpoints drifting out
    of sync on how scoring is applied."""
    raw = get_debris_field(force_refresh=force_refresh)
    return score_debris_field(raw, weights=weights or DEFAULT_WEIGHTS)


class PlanRequest(BaseModel):
    start_altitude_km: float = Field(..., description="Spacecraft's current orbit altitude, km")
    start_inclination_deg: float = Field(..., description="Spacecraft's current orbit inclination, deg")
    fuel_budget_km_s: float = Field(..., gt=0, description="Total delta-v budget for the mission, km/s")
    pool_size: int = Field(DEFAULT_POOL_SIZE, gt=0, description="How many top-risk candidates the optimizer considers")
    risk_penalty_scale: float = Field(RISK_PENALTY_SCALE, description="Tuning knob: higher = solver skips fewer risky nodes even if fuel-expensive")
    weights: Optional[dict[str, float]] = Field(None, description="Override risk_score.py DEFAULT_WEIGHTS, e.g. {'proximity': 0.8, 'lifetime': 0.2}")


class ReplanRequest(PlanRequest):
    user_request_text: str = Field(..., description="Plain-English override instructions, e.g. 'use only 1.5 km/s of fuel'")


@app.get("/debris-field")
def debris_field(force_refresh: bool = False):
    """Full scored, risk-ranked debris list (riskiest first)."""
    return _get_scored_field(force_refresh=force_refresh)


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

    pool = select_candidate_pool(scored, pool_size=req.pool_size)

    result = optimize_route(
        pool,
        fuel_budget_km_s=req.fuel_budget_km_s,
        start_altitude_km=req.start_altitude_km,
        start_inclination_deg=req.start_inclination_deg,
        risk_penalty_scale=req.risk_penalty_scale,
    )

    if "error" in result:
        raise HTTPException(status_code=422, detail=result["error"])

    # OR-Tools can return a valid (non-None) solution that visits zero nodes
    # when the per-skip penalty is below the cheapest arc cost -- the solver
    # finds it optimal to skip everything and pays no fuel.  That is not an
    # error from the solver's perspective, so "error" is never set and the
    # guard above doesn't fire.  Flag it explicitly so callers always get a
    # human-readable explanation rather than a silent empty route.
    if result["visited_count"] == 0:
        result["warning"] = (
            "No debris nodes were visited within the given constraints. "
            "Possible causes: fuel_budget_km_s is too tight to reach any "
            "candidate (cheapest hop on this pool is ~0.004 km/s), or "
            "risk_penalty_scale is too low relative to arc costs (threshold "
            "~2-3 on the 700-1000km pool), making it cheaper for the solver "
            "to skip every node. Try raising fuel_budget_km_s or "
            "risk_penalty_scale."
        )

    result["pool_size_used"] = len(pool)
    return result


@app.post("/plan")
def plan(req: PlanRequest):
    """Risk-ranked pool -> orienteering optimizer -> route + reasoning-ready breakdown."""
    return _run_plan(req)


# ---------------------------------------------------------------------------
# /replan helpers
# ---------------------------------------------------------------------------

# groq==0.11.0 supports response_format={"type": "json_object"} only (no
# json_schema mode). The JSON schema is described in the system prompt instead.
_GROQ_TIMEOUT = 20.0
_ALLOWED_OVERRIDE_KEYS = {"fuel_budget_km_s", "risk_penalty_scale", "weights", "no_changes"}

def _build_parse_prompt(req: "PlanRequest") -> str:
    """Build the system prompt with current parameter values embedded so the
    model can resolve relative instructions like 'cut in half' or 'double it'."""
    base_weights = req.weights or DEFAULT_WEIGHTS
    return (
        "You are a parameter-extraction assistant for an orbital debris removal mission planner. "
        "The mission currently has these parameter values:\n"
        f"  fuel_budget_km_s   = {req.fuel_budget_km_s}  (also called: fuel budget, delta-v budget, fuel limit)\n"
        f"  risk_penalty_scale = {req.risk_penalty_scale}  (also called: risk penalty, risk weight, risk aggressiveness)\n"
        f"  weights.proximity  = {base_weights.get('proximity', DEFAULT_WEIGHTS['proximity'])}  (also called: proximity weight, congestion weight)\n"
        f"  weights.lifetime   = {base_weights.get('lifetime',  DEFAULT_WEIGHTS['lifetime'])}  (also called: lifetime weight, drag weight)\n"
        "\n"
        "From the user's message, extract ONLY the parameters they want to change and output a single valid JSON object. "
        "The only keys you may emit are:\n"
        "  fuel_budget_km_s   -- positive float (km/s)\n"
        "  risk_penalty_scale -- non-negative float\n"
        "  weights            -- object with keys 'proximity' (float 0-1) and/or 'lifetime' (float 0-1)\n"
        "\n"
        "Rules:\n"
        "- Resolve relative instructions using the current values shown above "
        "(e.g. 'cut in half' -> divide the current value by 2; 'double it' -> multiply by 2; "
        "'reduce by 20%' -> multiply by 0.8).\n"
        "- Omit any key the user did not mention.\n"
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


def _parse_overrides(user_text: str, req: "PlanRequest") -> dict[str, Any]:
    """Call openai/gpt-oss-20b in json_object mode to extract parameter overrides.
    Retries once on malformed JSON, then raises ValueError."""
    client = _groq_client()
    system_prompt = _build_parse_prompt(req)
    last_raw = ""
    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model="openai/gpt-oss-20b",
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
        except json.JSONDecodeError:
            if attempt == 1:
                raise ValueError(
                    f"LLM returned malformed JSON after retry. Raw response: {last_raw!r}"
                )
            # first attempt failed -- retry once
        except (groq_module.APIConnectionError, groq_module.RateLimitError) as exc:
            # APIConnectionError is the base of APITimeoutError — catches both
            # a clean timeout and cases where the connection is refused/reset
            # before the timeout fires (e.g. absurdly low timeout values).
            raise HTTPException(
                status_code=503,
                detail=f"Groq API unavailable (timeout or connection error): {exc}",
            ) from exc
    return {}  # unreachable; satisfies type-checker


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
        "Output only the explanation -- no JSON, no markdown.\n\n"
        + json.dumps(diff)
    )
    last_raw = ""
    for attempt in range(2):
        try:
            resp = _groq_client().chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            last_raw = resp.choices[0].message.content or ""
            return last_raw.strip()
        except json.JSONDecodeError:
            if attempt == 1:
                raise ValueError(
                    f"Explanation LLM returned malformed response after retry. Raw: {last_raw!r}"
                )
        except (groq_module.APIConnectionError, groq_module.RateLimitError) as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Groq API unavailable (timeout or connection error): {exc}",
            ) from exc
    return ""  # unreachable


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
        # optimizer.py:95 converts the budget to an integer via
        # round(fuel_budget_km_s * 1000).  Any value below 0.0005 rounds to 0,
        # setting OR-Tools' Fuel dimension capacity to zero -- no arc can be
        # traversed, so the solver returns a valid non-None solution that visits
        # nothing.  That empty route comes back as a silent 200 with no error
        # key (same degeneration path as a sub-threshold risk_penalty_scale).
        # 0.001 gives 2× margin above the 0.0005 rounding cliff.
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

    if "risk_penalty_scale" in parsed:
        v = float(parsed["risk_penalty_scale"])
        if v < 0:
            raise HTTPException(status_code=422, detail="risk_penalty_scale must be >= 0")
        # The degeneration threshold is pool- and start-position-dependent:
        # with a same-inclination start, visits drop to 0 below rps~2-3; with
        # a cross-inclination start, they collapse to near-zero below rps~50.
        # This floor is therefore a best-effort filter, not a guarantee -- the
        # warning field on visited_count==0 (main.py:~106) is the real safety
        # net.  50 is chosen as the floor: it cleared the degeneration zone in
        # all start configurations tested on the 700-1000km pool (rps=5 still
        # gave 1 visit from a cross-inclination start; rps=50 gave 7+).
        if v < 50:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"risk_penalty_scale must be >= 50 (got {v}). "
                    "The degeneration threshold (where OR-Tools skips all nodes "
                    "and returns a silent empty route) is pool- and "
                    "start-position-dependent; 50 is the validated safe floor "
                    "on the 700-1000km debris pool."
                ),
            )
        overrides["risk_penalty_scale"] = v

    if "weights" in parsed:
        base_weights = req.weights or DEFAULT_WEIGHTS
        w_raw = parsed["weights"]
        has_p = "proximity" in w_raw
        has_l = "lifetime"  in w_raw
        p = float(w_raw["proximity"]) if has_p else float(base_weights.get("proximity", DEFAULT_WEIGHTS["proximity"]))
        l = float(w_raw["lifetime"])  if has_l else float(base_weights.get("lifetime",  DEFAULT_WEIGHTS["lifetime"]))
        if not (0.0 <= p <= 1.0) or not (0.0 <= l <= 1.0):
            raise HTTPException(status_code=422, detail="Weight values must be in [0, 1]")
        total = p + l
        if abs(total - 1.0) > 1e-6:
            if has_p and not has_l:
                l = 1.0 - p    # honour the explicit value exactly, derive the other
            elif has_l and not has_p:
                p = 1.0 - l
            else:
                p, l = p / total, l / total  # both given but don't sum to 1 → normalize
        overrides["weights"] = {"proximity": round(p, 6), "lifetime": round(l, 6)}

    # ------------------------------------------------------------------ #
    # Step 4 -- compute old plan (original params) and new plan (merged)  #
    # ------------------------------------------------------------------ #
    print(f"[replan] req.model_dump() before old_plan: {req.model_dump()}", flush=True)
    old_plan = _run_plan(req)

    new_req_data = req.model_dump()
    new_req_data.update(overrides)
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

    # ------------------------------------------------------------------ #
    # Step 6 -- ask large LLM for plain-language explanation (diff only)  #
    # ------------------------------------------------------------------ #
    try:
        explanation = _explain_diff(diff)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "old_plan":         old_plan,
        "new_plan":         new_plan,
        "diff":             diff,
        "explanation":      explanation,
        "overrides_applied": overrides,
    }


@app.get("/naive-route")
def naive_route(start_altitude_km: float, start_inclination_deg: float, fuel_budget_km_s: float, pool_size: int = DEFAULT_POOL_SIZE):
    """Nearest-neighbor baseline for the naive-vs-AI comparison (Week 5 Day 35).
    Greedy: always hop to whatever's cheapest next, ignore risk entirely,
    stop once the next hop would blow the budget. This is the strawman the
    optimizer's smarter risk-vs-fuel tradeoff gets compared against."""
    scored = _get_scored_field()
    pool = select_candidate_pool(scored, pool_size=pool_size)

    from app.cost_matrix import build_cost_matrix

    depot = {"norad_id": -1, "name": "DEPOT (spacecraft start)", "altitude_km": start_altitude_km, "inclination_deg": start_inclination_deg, "risk_score": 0.0}
    nodes = [depot] + pool
    matrix = build_cost_matrix(nodes)

    visited_idx: list[int] = []
    remaining = set(range(1, len(nodes)))
    current = 0
    fuel_used = 0.0
    steps: list[dict[str, Any]] = []

    while remaining:
        next_idx = min(remaining, key=lambda j: matrix[current][j])
        hop_cost = matrix[current][next_idx]
        if fuel_used + hop_cost > fuel_budget_km_s:
            break
        fuel_used += hop_cost
        steps.append({"from": nodes[current]["name"], "to": nodes[next_idx]["name"], "delta_v_km_s": round(hop_cost, 4)})
        visited_idx.append(next_idx)
        remaining.discard(next_idx)
        current = next_idx

    visited_objects = [nodes[i] for i in visited_idx]
    return {
        "route": [o["name"] for o in visited_objects],
        "visited_count": len(visited_objects),
        "skipped_count": len(pool) - len(visited_objects),
        "total_fuel_cost_km_s": round(fuel_used, 4),
        "fuel_budget_km_s": fuel_budget_km_s,
        "fuel_used_fraction": round(fuel_used / fuel_budget_km_s, 4) if fuel_budget_km_s > 0 else 0.0,
        "total_risk_collected": round(sum(o.get("risk_score", 0.0) for o in visited_objects), 4),
        "step_breakdown": steps,
    }
