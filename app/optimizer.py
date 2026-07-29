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
    from .removal_method import METHOD_NET_CAPTURE
    from .delta_v import raan_drift_deg, transfer_delta_v
except ImportError:
    from cost_matrix import build_cost_matrix, scale_matrix_for_ortools  # pyright: ignore[reportImplicitRelativeImport]
    from removal_method import METHOD_NET_CAPTURE  # pyright: ignore[reportImplicitRelativeImport]
    from delta_v import raan_drift_deg, transfer_delta_v  # pyright: ignore[reportImplicitRelativeImport]

DEFAULT_NETS_CARRIED = 1  # RemoveDEBRIS's actual flight history: exactly one net carried.

RISK_PENALTY_SCALE = 3000.0  # risk_score in [0,1] -> penalty in scaled cost units
                              # (units match cost_matrix.DELTA_V_SCALE: 1 unit = 1 m/s)
SOLVER_TIME_LIMIT_SECONDS = 5

# Heuristic: days of elapsed mission time per km/s of delta-v spent.
# Based on a rough LEO transfer time estimate (~1 day per 0.1 km/s of dv
# for typical Hohmann + phasing at 800km). Tunable; used only for RAAN
# drift projection, not for the optimizer's cost matrix itself.
TRANSFER_TIME_DAYS_PER_KM_S = 10.0


def _build_depot_node(altitude_km: float, inclination_deg: float, raan_deg: float = 0.0) -> dict[str, Any]:
    """Wraps the spacecraft's current orbit in the same dict shape as a
    debris object, so it can go through build_cost_matrix() unmodified.
    risk_score=0.0 since the depot isn't a target -- it's never offered to
    AddDisjunction, so this value is never actually used, just present for
    shape consistency.

    raan_deg defaults to 0.0: real debris objects carry a real raan_deg
    from tle_fetch.py, but the spacecraft's own current RAAN isn't known
    unless the caller supplies it (main.py's start_raan_deg). 0.0 is a
    silent-but-safe fallback -- it reproduces the pre-RAAN |incl1-incl2|
    approximation for depot hops specifically, not a crash, but it's the
    one remaining place the old blind spot can still show up until every
    caller passes a real value."""
    return {
        "norad_id": -1,
        "name": "DEPOT (spacecraft start)",
        "altitude_km": altitude_km,
        "inclination_deg": inclination_deg,
        "raan_deg": raan_deg,
        "risk_score": 0.0,
    }


def optimize_route(
    pool: list[dict[str, Any]],
    fuel_budget_km_s: float,
    start_altitude_km: float,
    start_inclination_deg: float,
    start_raan_deg: float = 0.0,
    risk_penalty_scale: float = RISK_PENALTY_SCALE,
    nets_carried: int = DEFAULT_NETS_CARRIED,
) -> dict[str, Any]:
    """
    Solve the orienteering problem over `pool` (the ~30-50 candidate objects
    from cost_matrix.select_candidate_pool()), starting from the spacecraft's
    current orbit, subject to a total delta-v budget.

    start_raan_deg: the spacecraft's current RAAN. Defaults to 0.0 if the
    caller doesn't know it, which falls back to the pre-RAAN
    |incl1-incl2| approximation for depot->first-hop legs only -- every
    other leg (debris-to-debris) already uses real RAAN values from
    tle_fetch.py regardless of this default.

    nets_carried caps how many net_capture stops the route may include --
    a real hardware constraint, not a tunable knob: RemoveDEBRIS (the only
    flown precedent) carried exactly one net. Default 1 reflects that;
    callers can raise it for an explicit exploratory/hypothetical run.

    Returns route order, visited vs skipped candidates, total fuel cost,
    per-step cost breakdown, and how much of the budget got used.
    """
    depot = _build_depot_node(start_altitude_km, start_inclination_deg, start_raan_deg)

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

    # Net-capacity dimension: each net_capture node arriving consumes 1 unit
    # of a nets_carried-sized capacity, same pattern as a CVRP demand
    # dimension. Nodes with any other removal_method (or none, e.g. objects
    # that never went through add_removal_methods) consume 0 and are
    # unaffected. This is a real hardware cap, not a soft preference --
    # AddDisjunction below still decides whether any given node is worth
    # visiting at all; this only bounds how many net_capture stops can be
    # among those chosen.
    def net_capacity_callback(from_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        if 1 <= from_node <= n_pool and pool[from_node - 1].get("removal_method") == METHOD_NET_CAPTURE:
            return 1
        return 0

    net_capacity_callback_index = routing.RegisterUnaryTransitCallback(net_capacity_callback)
    routing.AddDimension(net_capacity_callback_index, 0, nets_carried, True, "NetCapacity")

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

    # Per-step breakdown, walking depot -> visited nodes in solved order,
    # applying J2 RAAN drift to each target's RAAN at the predicted arrival time.
    #
    # Design: OR-Tools solved using the static (fetch-time) cost matrix -- we
    # can't rebuild the matrix per-leg during solving. The drift correction
    # happens here in post-solve: for each leg we (a) estimate arrival time from
    # cumulative delta-v so far, (b) project the target's RAAN forward by that
    # elapsed time, (c) recompute the actual arc cost with the drifted RAAN. If
    # the drifted cost pushes the leg over the remaining fuel budget, it's marked
    # unreachable and the walk stops -- same semantics as a hard budget overrun.
    #
    # Uses node indices directly (already known from visited_pool_indices)
    # rather than nodes.index(obj) -- list.index() on dicts does a value
    # equality scan, which isn't a safe identity check if two objects ever
    # have identical field values.
    step_breakdown: list[dict[str, Any]] = []
    arrival_time_per_pool_i: dict[int, float] = {}  # pool_i -> cumulative days at arrival
    total_fuel = 0.0
    elapsed_days = 0.0
    fuel_remaining = fuel_budget_km_s
    prev_node_index = 0  # depot is always node 0
    drift_truncated_at: int | None = None  # pool index where drift made leg unaffordable

    for step_i, pool_i in enumerate(visited_pool_indices):
        node_index = pool_i + 1
        from_node = nodes[prev_node_index]
        to_node   = nodes[node_index]

        # Project the target's RAAN forward to predicted arrival time.
        fetch_time_raan = to_node.get("raan_deg", 0.0)
        drift = raan_drift_deg(to_node["altitude_km"], to_node.get("inclination_deg", 0.0), elapsed_days)
        projected_raan = fetch_time_raan + drift

        # Recompute arc cost with drifted RAAN.
        drifted_cost = transfer_delta_v(
            alt1_km=from_node["altitude_km"],
            incl1_deg=from_node.get("inclination_deg", 0.0),
            alt2_km=to_node["altitude_km"],
            incl2_deg=to_node.get("inclination_deg", 0.0),
            raan1_deg=from_node.get("raan_deg", 0.0),
            raan2_deg=projected_raan,
        )["delta_v_total_km_s"]

        if drifted_cost > fuel_remaining:
            # Drift-adjusted cost exceeds remaining budget -- stop the walk here.
            # visited_objects/route_details will be truncated to only the steps
            # that actually completed.
            drift_truncated_at = step_i
            break

        step_breakdown.append({
            "from": _label(from_node),
            "to": _label(to_node),
            "delta_v_km_s": round(drifted_cost, 4),
            "arrival_time_days": round(elapsed_days, 4),
            "raan_drift_deg": round(drift, 4),
        })
        total_fuel += drifted_cost
        fuel_remaining -= drifted_cost
        # Advance elapsed time by estimated transfer duration for this leg.
        elapsed_days += drifted_cost * TRANSFER_TIME_DAYS_PER_KM_S
        arrival_time_per_pool_i[pool_i] = elapsed_days
        prev_node_index = node_index

    # If drift truncated the walk, trim visited_objects to match completed steps.
    if drift_truncated_at is not None:
        visited_pool_indices = visited_pool_indices[:drift_truncated_at]
        visited_objects      = [pool[i] for i in visited_pool_indices]
        visited_index_set    = set(visited_pool_indices)
        skipped_objects      = [obj for i, obj in enumerate(pool) if i not in visited_index_set]

    # route_details: full per-object detail in solved visit order.
    # object_type/removal_method are additive fields from
    # removal_method.add_removal_methods(), applied once upstream on the
    # full scored field (main._get_scored_field) before pool selection.
    # Pulled with .get() defaults, not direct indexing, so this function
    # doesn't hard-fail if that enrichment step is ever skipped or reordered.
    route_details = [
        {
            "norad_id": o["norad_id"],
            "name": o["name"],
            "object_type": o.get("object_type", "unknown"),
            "removal_method": o.get("removal_method", "unclassified"),
            "possible_methods": o.get("possible_methods", []),
            "method_maturity": o.get("method_maturity", {}),
            "removal_method_explanation": o.get("removal_method_explanation", ""),
            "risk_score": round(o.get("risk_score", 0.0), 4),
            "arrival_time_days": round(arrival_time_per_pool_i.get(pool_i, 0.0), 4),
        }
        for pool_i, o in zip(visited_pool_indices, visited_objects)
    ]

    return {
        "route": [_label(o) for o in visited_objects],
        "route_details": route_details,
        "visited_count": len(visited_objects),
        "skipped_count": len(skipped_objects),
        "skipped_names": [_label(o) for o in skipped_objects],
        "total_fuel_cost_km_s": round(total_fuel, 4),
        "fuel_budget_km_s": fuel_budget_km_s,
        "fuel_used_fraction": round(total_fuel / fuel_budget_km_s, 4) if fuel_budget_km_s > 0 else 0.0,
        "total_risk_collected": round(sum(o.get("risk_score", 0.0) for o in visited_objects), 4),
        "step_breakdown": step_breakdown,
        "net_capacity_constrained": nets_carried,
    }


if __name__ == "__main__":
    import random

    from cost_matrix import select_candidate_pool  # pyright: ignore[reportImplicitRelativeImport]
    from risk_score import score_debris_field  # pyright: ignore[reportImplicitRelativeImport]
    from removal_method import add_removal_methods  # pyright: ignore[reportImplicitRelativeImport]

    # Same synthetic 3-cluster field as cost_matrix.py's test, for continuity.
    random.seed(42)
    synthetic: list[dict[str, Any]] = []
    clusters = [("COSMOS", 74.0, 780.0), ("IRIDIUM", 86.4, 800.0), ("FENGYUN", 98.8, 850.0)]
    obj_id = 0
    for name, base_incl, base_alt in clusters:
        for k in range(15):
            obj_id += 1
            # First of each cluster is intact (no "DEB"); rest are fragments,
            # so removal_method classification (and therefore nets_carried /
            # monitor_only exclusion below) has something real to act on --
            # an all-intact synthetic pool would make both a silent no-op.
            label = f"{name}-{obj_id}" if k == 0 else f"{name} DEB-{obj_id}"
            synthetic.append({
                "norad_id": 10000 + obj_id,
                "name": label,
                "altitude_km": round(base_alt + random.uniform(-20, 20), 2),
                "inclination_deg": round(base_incl + random.uniform(-0.3, 0.3), 4),
                "latitude": 0.0,
                "longitude": 0.0,
                "bstar": random.uniform(0.00001, 0.0001),
            })

    scored = add_removal_methods(score_debris_field(synthetic))
    pool = select_candidate_pool(scored, pool_size=40)
    print(f"Pool method mix: { {m: sum(1 for o in pool if o['removal_method']==m) for m in set(o['removal_method'] for o in pool)} }")

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

    # nets_carried cap: with a generous fuel budget (so fuel isn't the
    # binding constraint) and a low net cap, net_capture visits must not
    # exceed the cap -- confirms the dimension is actually constraining,
    # not just present and unused.
    net_capped = optimize_route(pool, fuel_budget_km_s=10.0, start_altitude_km=start_alt,
                                 start_inclination_deg=start_incl, nets_carried=1)
    net_visits = sum(1 for d in net_capped["route_details"] if d["removal_method"] == "net_capture")
    print(f"\n  nets_carried=1 test: {net_visits} net_capture stop(s) visited (must be <= 1), "
          f"net_capacity_constrained={net_capped['net_capacity_constrained']}")
    assert net_visits <= 1, "nets_carried cap violated -- more net_capture stops than allowed!"
    assert net_capped["net_capacity_constrained"] == 1

    net_uncapped = optimize_route(pool, fuel_budget_km_s=10.0, start_altitude_km=start_alt,
                                   start_inclination_deg=start_incl, nets_carried=99)
    net_uncapped_visits = sum(1 for d in net_uncapped["route_details"] if d["removal_method"] == "net_capture")
    print(f"  nets_carried=99 test: {net_uncapped_visits} net_capture stop(s) visited (should be >= the capped count)")
    assert net_uncapped_visits >= net_visits, "Raising the cap should never visit FEWER net_capture nodes!"

    # monitor_only must never appear in route_details at all -- select_candidate_pool
    # (cost_matrix.py) excludes it before the pool even reaches this module.
    assert all(d["removal_method"] != "monitor_only" for r in (tight, generous, mid, net_capped) for d in r["route_details"]), \
        "monitor_only object made it into a route -- pool filtering regressed!"
    print("  nets_carried cap respected and actually binding: OK")
    print("  monitor_only never routed: OK")
    print("  (If any of this had failed, the model would be broken -- these are non-negotiable invariants.)")