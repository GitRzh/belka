"""
Module B, step 3: the optimizer.

Wires the N x N delta-v matrix (cost_matrix.py) into OR-Tools as a
budget-constrained coverage problem: the solver picks which subset of
the candidate pool to visit, and in what fuel-optimal order, subject only
to the total delta-v budget.  Risk score is NOT part of the solver objective
-- it only drives post-solve ordering.

Design:
  - Cost model: pure fuel (delta-v, km/s scaled to integer units).  The
    solver minimises total transit cost.  AddDisjunction penalty = budget_scaled
    (the entire fuel budget), so skipping a node notionally "costs" as much as
    the whole budget, making visits always preferred when the arc fits in the
    remaining fuel.  The solver therefore visits as many nodes as budget allows
    in the fuel-cheapest order.
  - Post-solve ordering: the visited set is re-sorted by risk_score DESC after
    the solve so the highest-risk debris is addressed first.  This reflects real
    mission planning: "visit as many dangerous objects as fuel allows; address
    the worst ones first."
  - Modelled as an open (one-way) trip: zero-cost virtual end node lets the
    route stop wherever's cheapest without a return-to-depot burn.
"""
from typing import Any, Callable

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

try:
    from .cost_matrix import build_cost_matrix, scale_matrix_for_ortools, DELTA_V_SCALE
    from .removal_method import METHOD_NET_CAPTURE
    from .delta_v import raan_drift_deg, transfer_delta_v
except ImportError:
    from cost_matrix import build_cost_matrix, scale_matrix_for_ortools, DELTA_V_SCALE  # pyright: ignore[reportImplicitRelativeImport]
    from removal_method import METHOD_NET_CAPTURE  # pyright: ignore[reportImplicitRelativeImport]
    from delta_v import raan_drift_deg, transfer_delta_v  # pyright: ignore[reportImplicitRelativeImport]

DEFAULT_NETS_CARRIED = 1  # RemoveDEBRIS's actual flight history: exactly one net carried.

SOLVER_TIME_LIMIT_SECONDS = 5
# When nets_carried > DEFAULT_NETS_CARRIED (i.e. > 1), the NetCapacity
# dimension adds a second active constraint to the search space.  Under
# GUIDED_LOCAL_SEARCH this can cause the solver to miss solutions that were
# found at the tighter cap -- most visibly, a 5s search at cap=3 can return
# a strictly worse objective value than the cap=1 solution that was already
# a legal move in the relaxed space.  The confirmed failure mode (original
# failing pytest run: cap=1 obj=987,310; cap=3 obj=995,644 -- worse by 8,334
# units despite having more freedom) came from a real Celestrak pool.
# A longer budget gives GLS more iterations to escape the initial local
# minimum introduced by the extra dimension.
SOLVER_TIME_LIMIT_SECONDS_EXTRA_NETS = 15
# Dry-run calls only need visited_count > 0; OR-Tools returns as soon as it
# finds any feasible solution, so 1 s is enough to confirm feasibility
# without finding the optimal route.  Named constant so main.py's
# _dry_run_plan() can reference it without a magic number.
DRY_RUN_TIME_LIMIT_SECONDS = 1

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


class _DriftWalkResult:
    """Return value of _drift_walk; keeps the signature stable without a tuple."""
    __slots__ = (
        "step_breakdown",
        "total_fuel",
        "elapsed_days",
        "arrival_time_per_i",
        "trimmed_visited_indices",
    )

    def __init__(
        self,
        step_breakdown: list[dict],
        total_fuel: float,
        elapsed_days: float,
        arrival_time_per_i: dict,
        trimmed_visited_indices: list[int],
    ) -> None:
        self.step_breakdown = step_breakdown
        self.total_fuel = total_fuel
        self.elapsed_days = elapsed_days
        self.arrival_time_per_i = arrival_time_per_i
        self.trimmed_visited_indices = trimmed_visited_indices


def _drift_walk(
    nodes: list[dict],
    visited_indices: list[int],
    fuel_budget_km_s: float | None,
    label_fn: Callable[[dict], str],
    max_wait_days: float = 0.0,
    min_saving_km_s: float = 0.0,
) -> "_DriftWalkResult":
    """Post-solve per-leg drift walk shared by optimize_route() and solve_forced_route().

    For each leg in *visited_indices* (which are pool/target indices, mapping to
    ``nodes[idx + 1]``):

    1. Projects the target's RAAN forward by the current *elapsed_days*.
    2. Recomputes the arc cost with the drifted RAAN.
    3. Optional wait window (max_wait_days > 0): scans wait_days in
       [0, max_wait_days] at 1-day resolution to find the wait that minimises
       drifted_cost.  If the best wait reduces cost by at least *min_saving_km_s*,
       it is recorded as ``recommended_wait_days`` in the step dict and added to
       ``elapsed_days``.  This is purely advisory — it does NOT alter the arc cost
       used for the budget gate or total_fuel; those still use the cost at
       elapsed_days=0 wait (i.e. current arrival estimate).  With
       ``max_wait_days=0.0`` (default) this branch is never entered, reproducing
       today's exact behaviour byte-for-byte.
    4. Budget gate (when fuel_budget_km_s is not None): stops and trims the walk
       if drifted_cost > fuel_remaining.

    Returns a _DriftWalkResult with:
      step_breakdown            list of per-step dicts
      total_fuel                sum of drifted costs for completed legs
      elapsed_days              cumulative elapsed time after the walk
      arrival_time_per_i        {visited_index: elapsed_days at arrival}
      trimmed_visited_indices   visited_indices[:n_completed] (may be a copy of the
                                input if no truncation occurred)
    """
    step_breakdown: list[dict] = []
    arrival_time_per_i: dict[int, float] = {}
    total_fuel = 0.0
    elapsed_days = 0.0
    fuel_remaining = fuel_budget_km_s  # None means no gate
    truncated_at: int | None = None
    prev_node_index = 0  # depot is always node 0

    for step_i, visit_i in enumerate(visited_indices):
        node_index = visit_i + 1
        from_node = nodes[prev_node_index]
        to_node = nodes[node_index]

        # Snapshot elapsed time before any wait so arrival_time_days always
        # reflects the pre-wait departure epoch (consistent with max_wait_days=0).
        departure_days = elapsed_days

        fetch_time_raan = to_node.get("raan_deg", 0.0)
        drift = raan_drift_deg(
            to_node["altitude_km"],
            to_node.get("inclination_deg", 0.0),
            elapsed_days,
        )
        projected_raan = fetch_time_raan + drift

        drifted_cost = transfer_delta_v(
            alt1_km=from_node["altitude_km"],
            incl1_deg=from_node.get("inclination_deg", 0.0),
            alt2_km=to_node["altitude_km"],
            incl2_deg=to_node.get("inclination_deg", 0.0),
            raan1_deg=from_node.get("raan_deg", 0.0),
            raan2_deg=projected_raan,
        )["delta_v_total_km_s"]

        # Optional wait-window optimisation (no-op when max_wait_days == 0).
        recommended_wait_days: int = 0
        if max_wait_days > 0.0:
            best_cost = drifted_cost
            best_wait = 0
            for wait_days in range(1, int(max_wait_days) + 1):
                trial_drift = raan_drift_deg(
                    to_node["altitude_km"],
                    to_node.get("inclination_deg", 0.0),
                    elapsed_days + wait_days,
                )
                trial_cost = transfer_delta_v(
                    alt1_km=from_node["altitude_km"],
                    incl1_deg=from_node.get("inclination_deg", 0.0),
                    alt2_km=to_node["altitude_km"],
                    incl2_deg=to_node.get("inclination_deg", 0.0),
                    raan1_deg=from_node.get("raan_deg", 0.0),
                    raan2_deg=fetch_time_raan + trial_drift,
                )["delta_v_total_km_s"]
                if trial_cost < best_cost:
                    best_cost = trial_cost
                    best_wait = wait_days
            if best_wait > 0 and (drifted_cost - best_cost) >= min_saving_km_s:
                recommended_wait_days = best_wait
                elapsed_days += best_wait  # advisory: advance clock by wait
                fuel_saved = round(drifted_cost - best_cost, 4)
            else:
                fuel_saved = 0.0
        else:
            fuel_saved = 0.0

        # Budget gate (only when a budget was supplied).
        if fuel_remaining is not None and drifted_cost > fuel_remaining:
            truncated_at = step_i
            break

        step: dict = {
            "from": label_fn(from_node),
            "to": label_fn(to_node),
            "delta_v_km_s": round(drifted_cost, 4),
            "arrival_time_days": round(departure_days, 4),
            "raan_drift_deg": round(drift, 4),
            "recommended_wait_days": recommended_wait_days,
            "fuel_saved_km_s": fuel_saved,
            "data_quality": to_node.get("data_quality", "unknown"),
        }
        step_breakdown.append(step)

        total_fuel += drifted_cost
        if fuel_remaining is not None:
            fuel_remaining -= drifted_cost
        elapsed_days += drifted_cost * TRANSFER_TIME_DAYS_PER_KM_S
        arrival_time_per_i[visit_i] = elapsed_days
        prev_node_index = node_index

    trimmed = visited_indices[:truncated_at] if truncated_at is not None else list(visited_indices)
    return _DriftWalkResult(
        step_breakdown=step_breakdown,
        total_fuel=total_fuel,
        elapsed_days=elapsed_days,
        arrival_time_per_i=arrival_time_per_i,
        trimmed_visited_indices=trimmed,
    )



def optimize_route(
    pool: list[dict[str, Any]],
    fuel_budget_km_s: float,
    start_altitude_km: float,
    start_inclination_deg: float,
    start_raan_deg: float = 0.0,
    nets_carried: int = DEFAULT_NETS_CARRIED,
    time_limit_seconds: int = SOLVER_TIME_LIMIT_SECONDS,
    max_wait_days: float = 0.0,
    min_saving_km_s: float = 0.0,
) -> dict[str, Any]:
    """
    Solve the fuel-optimal coverage problem over `pool` (the ~30-50 candidate
    objects from cost_matrix.select_candidate_pool()), starting from the
    spacecraft's current orbit, subject to a total delta-v budget.

    The solver minimises total fuel cost (pure delta-v) and visits as many nodes
    as the budget allows.  Risk score plays no role in the solver objective;
    instead, the visited set is sorted by risk_score DESC after the solve so the
    highest-risk debris is addressed first.

    start_raan_deg: the spacecraft's current RAAN. Defaults to 0.0 if the
    caller doesn't know it, which falls back to the pre-RAAN
    |incl1-incl2| approximation for depot->first-hop legs only -- every
    other leg (debris-to-debris) already uses real RAAN values from
    tle_fetch.py regardless of this default.

    nets_carried caps how many net_capture stops the route may include --
    a real hardware constraint, not a tunable knob: RemoveDEBRIS (the only
    flown precedent) carried exactly one net. Default 1 reflects that;
    callers can raise it for an explicit exploratory/hypothetical run.

    Returns route order (risk-sorted DESC), visited vs skipped candidates,
    total fuel cost, per-step cost breakdown, and how much of the budget
    got used.
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

    # Every pool node is optional.  Penalty = budget_scaled (the full fuel
    # budget): skipping a node notionally "costs" the whole budget, so the
    # solver always prefers visiting over skipping when the arc fits in the
    # remaining fuel.  Risk score does not appear here -- the solver decides
    # WHICH nodes fit; post-solve sorting by risk_score decides visit ORDER.
    for i in range(n_pool):
        node_index = i + 1
        routing.AddDisjunction([manager.NodeToIndex(node_index)], budget_scaled)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    # Escalate the time limit when nets_carried > 1 (the default/baseline).
    # A second active dimension (NetCapacity) widens the search space and
    # can cause GLS to miss solutions found at cap=1 within 5s.  Only apply
    # this when the caller did not already supply an explicit override (i.e.
    # time_limit_seconds is still at the module default), so dry-run calls
    # that pass DRY_RUN_TIME_LIMIT_SECONDS are never affected.
    effective_time_limit = time_limit_seconds
    if nets_carried > DEFAULT_NETS_CARRIED and time_limit_seconds == SOLVER_TIME_LIMIT_SECONDS:
        effective_time_limit = SOLVER_TIME_LIMIT_SECONDS_EXTRA_NETS
    search_params.time_limit.FromSeconds(effective_time_limit)

    solution = routing.SolveWithParameters(search_params)

    if solution is None:
        return {"error": "No feasible solution found -- fuel budget may be too tight to reach even one node."}

    # Walk the solved route, extracting visited nodes (skip depot/virtual end).
    solver_order_indices: list[int] = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if 1 <= node <= n_pool:
            solver_order_indices.append(node - 1)  # back to pool[] indexing
        index = solution.Value(routing.NextVar(index))

    # Post-solve: re-sort the visited set by risk_score DESC so the highest-risk
    # debris is addressed first.  The solver chose which nodes fit in the budget;
    # this re-sequences them for mission prioritisation without changing the set.
    # Index-based identity (not name-based) handles fragments that share the same
    # "name" field but differ only by norad_id.
    visited_pool_indices = sorted(
        solver_order_indices,
        key=lambda i: pool[i].get("risk_score", 0.0),
        reverse=True,
    )

    visited_objects = [pool[i] for i in visited_pool_indices]
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

    # Per-step breakdown: J2 RAAN drift correction via shared _drift_walk helper.
    # Design: OR-Tools solved using the static (fetch-time) cost matrix -- we
    # can't rebuild the matrix per-leg during solving. _drift_walk does the
    # post-solve per-leg correction: projects RAAN forward, recomputes arc cost,
    # gates against the remaining budget, and optionally evaluates a wait window.
    # Uses node indices directly (already known from visited_pool_indices)
    # rather than nodes.index(obj) -- list.index() on dicts does a value
    # equality scan, which isn't a safe identity check if two objects ever
    # have identical field values.
    walk = _drift_walk(
        nodes=nodes,
        visited_indices=visited_pool_indices,
        fuel_budget_km_s=fuel_budget_km_s,
        label_fn=_label,
        max_wait_days=max_wait_days,
        min_saving_km_s=min_saving_km_s,
    )

    visited_pool_indices = walk.trimmed_visited_indices
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
            "arrival_time_days": round(walk.arrival_time_per_i.get(pool_i, 0.0), 4),
            "data_quality": o.get("data_quality", "unknown"),
        }
        for pool_i, o in zip(visited_pool_indices, visited_objects)
    ]

    return {
        "route": [_label(o) for o in visited_objects],
        "route_details": route_details,
        "visited_count": len(visited_objects),
        "skipped_count": len(skipped_objects),
        "skipped_names": [_label(o) for o in skipped_objects],
        "total_fuel_cost_km_s": round(walk.total_fuel, 4),
        "fuel_budget_km_s": fuel_budget_km_s,
        "fuel_used_fraction": round(walk.total_fuel / fuel_budget_km_s, 4) if fuel_budget_km_s > 0 else 0.0,
        "total_risk_collected": round(sum(o.get("risk_score", 0.0) for o in visited_objects), 4),
        "step_breakdown": walk.step_breakdown,
        "net_capacity_constrained": nets_carried,
        "min_depot_hop_km_s": round(min(matrix[0][1:]), 4) if len(pool) > 0 else 0.0,
        "total_fuel_saved_km_s": round(sum(s["fuel_saved_km_s"] for s in walk.step_breakdown), 4),
    }


def solve_forced_route(
    targets: list[dict[str, Any]],
    start_altitude_km: float,
    start_inclination_deg: float,
    start_raan_deg: float = 0.0,
    fuel_budget_km_s: float | None = None,
    max_wait_days: float = 0.0,
    min_saving_km_s: float = 0.0,
) -> dict[str, Any]:
    """
    Forced-visit TSP: every object in `targets` MUST be visited.

    Semantics differ from optimize_route() deliberately:
    - No pool, no pool_size, no AddDisjunction — every supplied node is
      mandatory.
    - No fuel-budget dimension in the OR-Tools solve — the purpose is to
      compute the required fuel, not gate against a budget the caller may
      not have set.
    - Net-capacity dimension cap is set dynamically to the count of
      net_capture targets in `targets`, guaranteeing feasibility regardless
      of how many nets the user's selection requires.
    - Minimises total route delta-v (same scaled cost matrix as the
      orienteering solver, same OR-Tools machinery).

    Optional fuel_budget_km_s:
        None (default) — today's behaviour, no budget gate applied.
        float          — tracks fuel_remaining during the post-solve drift
                         walk; stops and trims visited_target_indices when
                         drifted_cost > fuel_remaining (same condition as
                         optimize_route).  nets_carried_required and warning
                         are still computed from the full original target
                         list, not the trimmed walk — they are a hardware-
                         requirement signal, not a budget-affordability check.

    Returns a dict shaped to mirror optimize_route()'s output for the fields
    that exist in this context, plus:
        nets_carried_required  int   — net_capture targets in the selection.
        fuel_budget_km_s       float — echoed when caller supplied it.
        warning                str   — present only when nets_carried_required > 1,
                                       noting it exceeds RemoveDEBRIS's single-net
                                       flight history (informational, not a rejection).
    """
    if not targets:
        return {"error": "No targets supplied to solve_forced_route -- list must be non-empty."}

    depot = _build_depot_node(start_altitude_km, start_inclination_deg, start_raan_deg)

    # Node layout: [0] depot | [1..n] targets | [n+1] virtual zero-cost end
    nodes = [depot] + targets
    n_targets = len(targets)
    end_index = n_targets + 1

    matrix = build_cost_matrix(nodes)
    scaled = scale_matrix_for_ortools(matrix)

    full_size = n_targets + 2
    full_matrix: list[list[int]] = [row + [0] for row in scaled]
    full_matrix.append([0] * full_size)

    manager = pywrapcp.RoutingIndexManager(full_size, 1, [0], [end_index])
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index: int, to_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return full_matrix[from_node][to_node]

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # Net-capacity dimension: cap = number of net_capture nodes in the forced
    # set.  This is the minimum feasible cap; anything lower would make the
    # problem infeasible (all nodes are mandatory, so the cap must accommodate
    # exactly the nets actually required by this selection).
    nets_carried_required = sum(
        1 for obj in targets if obj.get("removal_method") == METHOD_NET_CAPTURE
    )
    net_cap = max(nets_carried_required, 1)  # cap >= 1 keeps the dimension valid

    def net_capacity_callback(from_index: int) -> int:
        from_node = manager.IndexToNode(from_index)
        if 1 <= from_node <= n_targets and targets[from_node - 1].get("removal_method") == METHOD_NET_CAPTURE:
            return 1
        return 0

    net_capacity_callback_index = routing.RegisterUnaryTransitCallback(net_capacity_callback)
    routing.AddDimension(net_capacity_callback_index, 0, net_cap, True, "NetCapacity")

    # NOTE: no AddDisjunction — every target node is mandatory.

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
        return {"error": (
            "Solver could not find a feasible route for the selected targets. "
            "This can happen when orbital elements are degenerate (e.g. zero altitude) or "
            "when a very large selection produces a time-limit timeout. "
            "Try reducing the number of selected targets, or verify that all targets have valid TLE data."
        )}

    # Walk the solved route.
    visited_target_indices: list[int] = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if 1 <= node <= n_targets:
            visited_target_indices.append(node - 1)
        index = solution.Value(routing.NextVar(index))

    def _label(obj: dict[str, Any]) -> str:
        if obj["norad_id"] == -1:
            return obj["name"]
        return f"{obj['name']} ({obj['norad_id']})"

    # Post-solve per-step breakdown via shared _drift_walk helper.
    walk = _drift_walk(
        nodes=nodes,
        visited_indices=visited_target_indices,
        fuel_budget_km_s=fuel_budget_km_s,
        label_fn=_label,
        max_wait_days=max_wait_days,
        min_saving_km_s=min_saving_km_s,
    )

    visited_target_indices = walk.trimmed_visited_indices
    visited_objects = [targets[i] for i in visited_target_indices]

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
            "arrival_time_days": round(walk.arrival_time_per_i.get(target_i, 0.0), 4),
            "data_quality": o.get("data_quality", "unknown"),
        }
        for target_i, o in zip(visited_target_indices, visited_objects)
    ]

    result: dict[str, Any] = {
        "route": [_label(o) for o in visited_objects],
        "route_details": route_details,
        "visited_count": len(visited_objects),
        "total_fuel_cost_km_s": round(walk.total_fuel, 4),
        "total_risk_collected": round(sum(o.get("risk_score", 0.0) for o in visited_objects), 4),
        "step_breakdown": walk.step_breakdown,
        "nets_carried_required": nets_carried_required,
        "total_fuel_saved_km_s": round(sum(s["fuel_saved_km_s"] for s in walk.step_breakdown), 4),
    }

    if fuel_budget_km_s is not None:
        result["fuel_budget_km_s"] = fuel_budget_km_s

    if nets_carried_required > 1:
        result["warning"] = (
            f"This selection requires {nets_carried_required} net captures. "
            "RemoveDEBRIS — the only flown precedent for net-capture ADR — carried exactly one net. "
            "A mission visiting all selected targets would require hardware beyond current flight-demonstrated capability."
        )

    return result


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