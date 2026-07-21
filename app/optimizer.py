"""
Module B, step 3: the optimizer.

Wires the N x N delta-v matrix (cost_matrix.py) into OR-Tools as an
orienteering problem (prize-collecting TSP), NOT a forced-visit TSP: the
solver gets a fuel budget and *chooses* which subset of the candidate pool
to visit and in what order, to maximize total risk-value removed within
that budget. A plain TSP would be blind to medium-risk debris that happens
to sit conveniently along the route -- orienteering isn't.

Modeled as an open (one-way) routing problem: real depot at the servicing
spacecraft's current orbit, plus a zero-cost virtual "mission complete" end
node every real node connects to for free. That end node is what lets the
route terminate wherever's cheapest instead of forcing a return-to-depot
burn nobody asked for. AddDisjunction makes every debris node optional at a
per-node penalty = risk_score * RISK_PENALTY_SCALE -- skip a node and you
forfeit that penalty from the objective, so the solver only skips when the
marginal fuel cost of visiting genuinely exceeds the node's risk value.

RISK_PENALTY_SCALE is a tuning knob, same spirit as risk_score.py's
DEFAULT_WEIGHTS: meant to be overridden later by the /replan LLM parser
(e.g. "prioritize riskiest debris even if it costs more fuel" -> raise it).
"""
from typing import Any

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

try:
    from .cost_matrix import build_cost_matrix, scale_matrix_for_ortools
except ImportError:
    from cost_matrix import build_cost_matrix, scale_matrix_for_ortools  # pyright: ignore[reportImplicitRelativeImport]

RISK_PENALTY_SCALE = 3000.0  # risk_score in [0,1] -> penalty in scaled cost units
                              # (units match cost_matrix.DELTA_V_SCALE: 1 unit = 1 m/s)
SOLVER_TIME_LIMIT_SECONDS = 5


def _build_depot_node(altitude_km: float, inclination_deg: float) -> dict[str, Any]:
    """Wraps the spacecraft's current orbit in the same dict shape as a
    debris object, so it can go through build_cost_matrix() unmodified.
    risk_score=0.0 since the depot isn't a target -- it's never offered to
    AddDisjunction, so this value is never actually used, just present for
    shape consistency."""
    return {
        "norad_id": -1,
        "name": "DEPOT (spacecraft start)",
        "altitude_km": altitude_km,
        "inclination_deg": inclination_deg,
        "risk_score": 0.0,
    }


def optimize_route(
    pool: list[dict[str, Any]],
    fuel_budget_km_s: float,
    start_altitude_km: float,
    start_inclination_deg: float,
    risk_penalty_scale: float = RISK_PENALTY_SCALE,
) -> dict[str, Any]:
    """
    Solve the orienteering problem over `pool` (the ~30-50 candidate objects
    from cost_matrix.select_candidate_pool()), starting from the spacecraft's
    current orbit, subject to a total delta-v budget.

    Returns route order, visited vs skipped candidates, total fuel cost,
    per-step cost breakdown, and how much of the budget got used.
    """
    depot = _build_depot_node(start_altitude_km, start_inclination_deg)

    # Node layout: [0] depot (start) | [1..n] pool | [n+1] virtual end
    nodes = [depot] + pool
    n_pool = len(pool)
    end_index = n_pool + 1

    matrix = build_cost_matrix(nodes)  # (n_pool+1) x (n_pool+1) real costs
    scaled = scale_matrix_for_ortools(matrix)

    # Extend with the virtual end node: zero-cost from every real node,
    # so the tour can terminate anywhere without paying a return-to-depot burn.
    full_size = n_pool + 2
    full_matrix: list[list[int]] = [row + [0] for row in scaled]
    full_matrix.append([0] * full_size)  # outgoing arcs from end node are never used

    manager = pywrapcp.RoutingIndexManager(full_size, 1, [0], [end_index])
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return full_matrix[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    budget_scaled = round(fuel_budget_km_s * 1000)  # matches cost_matrix.DELTA_V_SCALE
    routing.AddDimension(transit_callback_index, 0, budget_scaled, True, "Fuel")

    # Every pool node (indices 1..n_pool) is optional at a risk-proportional penalty.
    for i, obj in enumerate(pool):
        node_index = i + 1
        risk = obj.get("risk_score", 0.0)
        penalty = round(risk * risk_penalty_scale)
        routing.AddDisjunction([manager.NodeToIndex(node_index)], penalty)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.FromSeconds(SOLVER_TIME_LIMIT_SECONDS)

    solution = routing.SolveWithParameters(search_params)

    if solution is None:
        return {"error": "No feasible solution found -- fuel budget may be too tight to reach even one node."}

    # Walk the solved route, extracting visited nodes (skip depot/virtual end).
    visited_pool_indices: list[int] = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if 1 <= node <= n_pool:
            visited_pool_indices.append(node - 1)  # back to pool[] indexing
        index = solution.Value(routing.NextVar(index))

    visited_objects = [pool[i] for i in visited_pool_indices]
    # NOTE: was previously computed via name-set difference, which silently
    # mis-reported skipped objects whenever two pool objects shared a "name"
    # (common in real data -- many debris fragments are all named e.g.
    # "COSMOS 2251 DEB", disambiguated only by norad_id). Index-based
    # difference is the correct identity check.
    visited_index_set = set(visited_pool_indices)
    skipped_objects = [obj for i, obj in enumerate(pool) if i not in visited_index_set]

    def _label(obj: dict[str, Any]) -> str:
        """Display label. Real debris fragments frequently share the same
        "name" field -- norad_id is the only unique identifier, so it's
        appended for anything that isn't the depot (norad_id -1, always
        unique/singular, no collision risk)."""
        if obj["norad_id"] == -1:
            return obj["name"]
        return f"{obj['name']} ({obj['norad_id']})"

    # Per-step breakdown, walking depot -> visited nodes in solved order.
    # Uses node indices directly (already known from visited_pool_indices)
    # rather than nodes.index(obj) -- list.index() on dicts does a value
    # equality scan, which isn't a safe identity check if two objects ever
    # have identical field values.
    step_breakdown: list[dict[str, Any]] = []
    total_fuel = 0.0
    prev_node_index = 0  # depot is always node 0
    for pool_i in visited_pool_indices:
        node_index = pool_i + 1
        result_matrix_lookup = matrix[prev_node_index][node_index]
        step_breakdown.append({
            "from": _label(nodes[prev_node_index]),
            "to": _label(nodes[node_index]),
            "delta_v_km_s": round(result_matrix_lookup, 4),
        })
        total_fuel += result_matrix_lookup
        prev_node_index = node_index

    return {
        "route": [_label(o) for o in visited_objects],
        "visited_count": len(visited_objects),
        "skipped_count": len(skipped_objects),
        "skipped_names": [_label(o) for o in skipped_objects],
        "total_fuel_cost_km_s": round(total_fuel, 4),
        "fuel_budget_km_s": fuel_budget_km_s,
        "fuel_used_fraction": round(total_fuel / fuel_budget_km_s, 4) if fuel_budget_km_s > 0 else 0.0,
        "total_risk_collected": round(sum(o.get("risk_score", 0.0) for o in visited_objects), 4),
        "step_breakdown": step_breakdown,
    }


if __name__ == "__main__":
    import random

    from cost_matrix import select_candidate_pool  # pyright: ignore[reportImplicitRelativeImport]
    from risk_score import score_debris_field  # pyright: ignore[reportImplicitRelativeImport]

    # Same synthetic 3-cluster field as cost_matrix.py's test, for continuity.
    random.seed(42)
    synthetic: list[dict[str, Any]] = []
    clusters = [("COSMOS", 74.0, 780.0), ("IRIDIUM", 86.4, 800.0), ("FENGYUN", 98.8, 850.0)]
    obj_id = 0
    for name, base_incl, base_alt in clusters:
        for _ in range(15):
            obj_id += 1
            synthetic.append({
                "norad_id": 10000 + obj_id,
                "name": f"{name}-{obj_id}",
                "altitude_km": round(base_alt + random.uniform(-20, 20), 2),
                "inclination_deg": round(base_incl + random.uniform(-0.3, 0.3), 4),
                "latitude": 0.0,
                "longitude": 0.0,
                "bstar": random.uniform(0.00001, 0.0001),
            })

    scored = score_debris_field(synthetic)
    pool = select_candidate_pool(scored, pool_size=40)

    # Placeholder start orbit -- in the real app this comes from the
    # spacecraft's actual current state, not a guess. Picked near the
    # COSMOS cluster here purely so the test has a plausible starting point.
    start_alt, start_incl = 800.0, 74.0

    print("=== Test 1: tight budget (0.5 km/s) -- should grab only cheap, high-value nearby targets ===")
    tight = optimize_route(pool, fuel_budget_km_s=0.5, start_altitude_km=start_alt, start_inclination_deg=start_incl)
    print(f"  Visited {tight['visited_count']}/{len(pool)}, fuel used {tight['total_fuel_cost_km_s']}/{tight['fuel_budget_km_s']} km/s ({tight['fuel_used_fraction']*100:.1f}%)")
    print(f"  Risk collected: {tight['total_risk_collected']}")
    print(f"  Route: {tight['route']}")

    print("\n=== Test 2: generous budget (10 km/s) -- should grab most/all of the pool ===")
    generous = optimize_route(pool, fuel_budget_km_s=10.0, start_altitude_km=start_alt, start_inclination_deg=start_incl)
    print(f"  Visited {generous['visited_count']}/{len(pool)}, fuel used {generous['total_fuel_cost_km_s']}/{generous['fuel_budget_km_s']} km/s ({generous['fuel_used_fraction']*100:.1f}%)")
    print(f"  Risk collected: {generous['total_risk_collected']}")
    print(f"  Skipped: {generous['skipped_names']}")

    print("\n=== Test 3: mid budget (2.5 km/s) -- the interesting case, per-step breakdown ===")
    mid = optimize_route(pool, fuel_budget_km_s=2.5, start_altitude_km=start_alt, start_inclination_deg=start_incl)
    print(f"  Visited {mid['visited_count']}/{len(pool)}, fuel used {mid['total_fuel_cost_km_s']}/{mid['fuel_budget_km_s']} km/s ({mid['fuel_used_fraction']*100:.1f}%)")
    print(f"  Risk collected: {mid['total_risk_collected']}")
    print("  Step-by-step:")
    for step in mid["step_breakdown"]:
        print(f"    {step['from']:<28} -> {step['to']:<12} {step['delta_v_km_s']:.4f} km/s")

    # Sanity checks that must hold regardless of the specific solution found.
    print("\n=== Sanity checks ===")
    assert tight["total_fuel_cost_km_s"] <= tight["fuel_budget_km_s"] + 1e-6, "Tight budget exceeded!"
    assert mid["total_fuel_cost_km_s"] <= mid["fuel_budget_km_s"] + 1e-6, "Mid budget exceeded!"
    assert generous["total_fuel_cost_km_s"] <= generous["fuel_budget_km_s"] + 1e-6, "Generous budget exceeded!"
    assert tight["visited_count"] <= mid["visited_count"] <= generous["visited_count"], \
        "More budget should never visit FEWER nodes -- monotonicity broken!"
    print("  Budget never exceeded: OK")
    print("  Visit count monotonically non-decreasing with budget: OK")
    print("  (If this had failed, the model would be broken -- these are non-negotiable invariants.)")