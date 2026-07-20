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
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.tle_fetch import get_debris_field
from app.risk_score import score_debris_field, DEFAULT_WEIGHTS
from app.cost_matrix import select_candidate_pool, DEFAULT_POOL_SIZE
from app.optimizer import optimize_route, RISK_PENALTY_SCALE

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


@app.post("/plan")
def plan(req: PlanRequest):
    """Risk-ranked pool -> orienteering optimizer -> route + reasoning-ready breakdown."""
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

    result["pool_size_used"] = len(pool)
    return result


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
