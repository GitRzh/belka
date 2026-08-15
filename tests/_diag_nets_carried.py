"""
Temporary diagnostic script — DO NOT delete until instructed.

Three phases:
  Phase 1: Live pool from cached field — print solution.ObjectiveValue() for
           caps 1/3/10 at 5s and confirm whether objectives are monotonically
           improving (lower = better).

  Phase 2: Mathematically-reconstructed objective confirmation from the
           original failing run's known numbers (cap=1: visited=2, transit=7.3105;
           cap=3: visited=1, transit=5.6443; pool=100, budget=10.0 km/s).

  Phase 3: Minimal synthetic pool that reliably forces the same failure pattern
           by construction — test whether 15s time_limit recovers the better solution.

Usage:
    PYTHONPATH=. venv/bin/python tests/_diag_nets_carried.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import copy
from typing import Any
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from app.main import _get_scored_field
from app.cost_matrix import select_candidate_pool, build_cost_matrix, scale_matrix_for_ortools
from app.optimizer import _build_depot_node
from app.removal_method import METHOD_NET_CAPTURE, METHOD_ROBOTIC_ARM_OR_NET

DEFAULT_START = dict(start_altitude_km=800.0, start_inclination_deg=74.0)
FUEL_BUDGET = 10.0
POOL_SIZE = 100


def _solve(pool: list[dict[str, Any]], nets_carried: int, time_limit_seconds: int,
           label: str) -> dict:
    depot = _build_depot_node(DEFAULT_START["start_altitude_km"],
                              DEFAULT_START["start_inclination_deg"])
    nodes = [depot] + pool
    n_pool = len(pool)
    end_idx = n_pool + 1

    matrix = build_cost_matrix(nodes)
    scaled = scale_matrix_for_ortools(matrix)
    full_size = n_pool + 2
    full_matrix = [row + [0] for row in scaled]
    full_matrix.append([0] * full_size)

    budget_scaled = round(FUEL_BUDGET * 1000)
    manager = pywrapcp.RoutingIndexManager(full_size, 1, [0], [end_idx])
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(fi: int, ti: int) -> int:
        return full_matrix[manager.IndexToNode(fi)][manager.IndexToNode(ti)]

    tc_idx = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(tc_idx)
    routing.AddDimension(tc_idx, 0, budget_scaled, True, "Fuel")

    def net_capacity_callback(fi: int) -> int:
        fn = manager.IndexToNode(fi)
        if 1 <= fn <= n_pool and pool[fn - 1].get("removal_method") == METHOD_NET_CAPTURE:
            return 1
        return 0

    nc_idx = routing.RegisterUnaryTransitCallback(net_capacity_callback)
    routing.AddDimension(nc_idx, 0, nets_carried, True, "NetCapacity")

    for i in range(n_pool):
        routing.AddDisjunction([manager.NodeToIndex(i + 1)], budget_scaled)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    params.time_limit.FromSeconds(time_limit_seconds)

    solution = routing.SolveWithParameters(params)
    if solution is None:
        print(f"  [{label}]  NO SOLUTION FOUND")
        return {}

    raw_obj = solution.ObjectiveValue()
    visited_pool_idxs = []
    idx = routing.Start(0)
    while not routing.IsEnd(idx):
        node = manager.IndexToNode(idx)
        if 1 <= node <= n_pool:
            visited_pool_idxs.append(node - 1)
        idx = solution.Value(routing.NextVar(idx))

    net_stops = sum(1 for i in visited_pool_idxs
                    if pool[i].get("removal_method") == METHOD_NET_CAPTURE)
    n_visited = len(visited_pool_idxs)
    n_skipped = n_pool - n_visited
    skip_penalty = n_skipped * budget_scaled
    transit_km_s = (raw_obj - skip_penalty) / 1000.0

    print(f"  [{label}]  "
          f"ObjectiveValue={raw_obj:>12,}  "
          f"transit_km_s={transit_km_s:>7.4f}  "
          f"visited={n_visited:>2}  net_stops={net_stops}  "
          f"skip_penalty={skip_penalty:>12,}  (n_skipped={n_skipped})")
    return {"obj": raw_obj, "visited": n_visited, "net_stops": net_stops,
            "transit_km_s": transit_km_s}


# =========================================================================== #
# Phase 1 — live pool
# =========================================================================== #
print("=" * 90)
print("Phase 1: Live pool — ObjectiveValue for caps 1/3/10 at 5s")
print("=" * 90)
print("Fetching debris field...", flush=True)
field = _get_scored_field()
print(f"Field size: {len(field)} objects", flush=True)
pool = select_candidate_pool(field, pool_size=POOL_SIZE)
net_in_pool = sum(1 for o in pool if o.get("removal_method") == METHOD_NET_CAPTURE)
print(f"Pool size: {len(pool)},  net_capture in pool: {net_in_pool}")
print(f"budget_scaled per node = {round(FUEL_BUDGET * 1000):,}\n")

live_5s = {}
for cap in (1, 3, 10):
    live_5s[cap] = _solve(pool, nets_carried=cap, time_limit_seconds=5,
                          label=f"cap={cap:>2}, 5s")

print()
print("Objective monotonicity check (lower=better; relaxed cap MUST give <= or lower obj):")
for cap in (3, 10):
    prev = 1 if cap == 3 else 3
    ok = live_5s[cap]["obj"] <= live_5s[prev]["obj"]
    print(f"  cap={cap} obj={live_5s[cap]['obj']:,} <= cap={prev} obj={live_5s[prev]['obj']:,}  "
          f"-> {'OK' if ok else 'REGRESSION'}")


# =========================================================================== #
# Phase 2 — math reconstruction of the original failing run's objective values
# =========================================================================== #
print()
print("=" * 90)
print("Phase 2: Arithmetic reconstruction of original failing run's objective values")
print("  (Original run: cap=1 -> visited=2 transit=7.3105; cap=3 -> visited=1 transit=5.6443)")
print("  pool_size=100, budget=10.0, budget_scaled=10000, 98 net_capture in pool")
print("=" * 90)

budget_scaled_orig = 10_000   # round(10.0 * 1000)
pool_size_orig     = 100

cap1_transit_scaled = round(7.3105 * 1000)   # 7311 (closest integer)
cap1_visited        = 2
cap1_skipped        = pool_size_orig - cap1_visited
cap1_skip_penalty   = cap1_skipped * budget_scaled_orig
cap1_obj            = cap1_transit_scaled + cap1_skip_penalty

cap3_transit_scaled = round(5.6443 * 1000)   # 5644
cap3_visited        = 1
cap3_skipped        = pool_size_orig - cap3_visited
cap3_skip_penalty   = cap3_skipped * budget_scaled_orig
cap3_obj            = cap3_transit_scaled + cap3_skip_penalty

print(f"  cap=1: transit_scaled={cap1_transit_scaled:,}  +  skip_penalty={cap1_skip_penalty:,} ({cap1_skipped} nodes × {budget_scaled_orig:,})  =  ObjectiveValue={cap1_obj:,}")
print(f"  cap=3: transit_scaled={cap3_transit_scaled:,}  +  skip_penalty={cap3_skip_penalty:,} ({cap3_skipped} nodes × {budget_scaled_orig:,})  =  ObjectiveValue={cap3_obj:,}")
print()
regression = cap3_obj > cap1_obj
print(f"  cap=3 ObjectiveValue ({cap3_obj:,}) {'>' if regression else '<='} cap=1 ObjectiveValue ({cap1_obj:,})")
print(f"  -> {'CONFIRMED OBJECTIVE REGRESSION: cap=3 solver returned a worse solution than cap=1' if regression else 'No regression'}")
if regression:
    print(f"  -> The cap=1 solution (transit=7.3105, visited=2) was LEGAL under cap=3 constraints.")
    print(f"     The solver failed to find it in 5s of GUIDED_LOCAL_SEARCH.")
    print(f"     Gap: cap=3 solution is {cap3_obj - cap1_obj:,} units worse than the solution it missed.")


# =========================================================================== #
# Phase 3 — synthetic pool: reproduce the objective regression, test 15s fix
#
# Strategy: construct a pool that forces the GLS search to reproduce the
# exact failure mode. Key insight from original failure:
#   - cap=1 found a 2-visit route: 1 arm object + 1 net_capture object
#   - cap=3 found a DIFFERENT 1-visit route with a cheaper transit
#   - The 1-visit route is also valid at cap=1, but cap=1's GLS found the
#     better 2-visit route; cap=3's GLS got stuck in the cheaper-transit
#     1-visit local minimum instead of exploring the 2-visit option.
#
# To force this: build a pool where:
#   - One arm object A is at incl=74.0 (same as depot): transit ≈ 0 from depot
#   - One net_capture object N is at incl=74.3: also very cheap (~0.3 km/s)
#   - All remaining 98 objects are expensive (incl=97.0, far from depot)
#
# cap=1 GLS PATH_CHEAPEST_ARC first solution: depot→A→N (0+ε transit, 1 net_capture ≤ 1)
# cap=3 GLS: larger search space, might find depot→N (0+ε, 1 net_capture ≤ 3) first
# and never improve past it, dropping A.
# =========================================================================== #

print()
print("=" * 90)
print("Phase 3: Synthetic pool designed to force the cap=1-better-than-cap=3 failure")
print("=" * 90)
print()

# Build the synthetic pool: arm object + net object both at near-depot orbit,
# then 98 expensive objects at high inclination.
synth_cheap_arm = {
    "norad_id": 90001, "name": "SYNTH_ARM_CHEAP (90001)",
    "altitude_km": 800.0, "inclination_deg": 74.0, "raan_deg": 0.0,
    "risk_score": 0.999, "removal_method": METHOD_ROBOTIC_ARM_OR_NET,
    "object_type": "intact", "possible_methods": [], "method_maturity": {},
    "removal_method_explanation": "", "epoch_age_days": 1.0, "data_quality": "fresh",
    "bstar": 0.0,
}
synth_cheap_net = {
    "norad_id": 90002, "name": "SYNTH_NET_CHEAP (90002)",
    "altitude_km": 800.0, "inclination_deg": 74.3, "raan_deg": 0.0,
    "risk_score": 0.998, "removal_method": METHOD_NET_CAPTURE,
    "object_type": "fragment", "possible_methods": [], "method_maturity": {},
    "removal_method_explanation": "", "epoch_age_days": 1.0, "data_quality": "fresh",
    "bstar": 0.0,
}
# 98 expensive nodes at high inclination (large plane change from depot)
expensive_nodes = [
    {
        "norad_id": 80000 + i, "name": f"EXPENSIVE_{i} ({80000+i})",
        "altitude_km": 800.0, "inclination_deg": 97.0 + (i % 5) * 0.1, "raan_deg": float(i * 3 % 360),
        "risk_score": 0.5 - i * 0.001, "removal_method": METHOD_NET_CAPTURE,
        "object_type": "fragment", "possible_methods": [], "method_maturity": {},
        "removal_method_explanation": "", "epoch_age_days": 1.0, "data_quality": "fresh",
        "bstar": 0.0,
    }
    for i in range(98)
]

synth_pool = [synth_cheap_arm, synth_cheap_net] + expensive_nodes
print(f"Pool: 1 cheap arm node (incl=74.0) + 1 cheap net node (incl=74.3) + 98 expensive nodes")
print(f"Total: {len(synth_pool)} nodes,  net_capture: {sum(1 for o in synth_pool if o['removal_method']==METHOD_NET_CAPTURE)}")
print()

print("--- 5s (production) ---")
synth_5s = {}
for cap in (1, 3, 10):
    synth_5s[cap] = _solve(synth_pool, nets_carried=cap, time_limit_seconds=5,
                           label=f"synth cap={cap:>2}, 5s")

print()
print("--- 15s ---")
synth_15s = {1: _solve(synth_pool, nets_carried=1, time_limit_seconds=5,
                       label="synth cap= 1,  5s (baseline)")}
for cap in (3, 10):
    synth_15s[cap] = _solve(synth_pool, nets_carried=cap, time_limit_seconds=15,
                            label=f"synth cap={cap:>2}, 15s")

print()
print("Regression check — 5s:")
for cap in (3, 10):
    prev = 1 if cap == 3 else 3
    ok = synth_5s[cap]["obj"] <= synth_5s[prev]["obj"]
    print(f"  cap={cap} obj={synth_5s[cap]['obj']:,} <= cap={prev} obj={synth_5s[prev]['obj']:,}  "
          f"-> {'OK' if ok else 'REGRESSION'}")

print()
print("Regression check — 15s:")
for cap in (3, 10):
    prev = 1 if cap == 3 else 3
    prev_r = synth_15s.get(prev) or synth_5s.get(prev)
    cap_r = synth_15s.get(cap)
    ok = cap_r["obj"] <= prev_r["obj"]
    print(f"  cap={cap} obj={cap_r['obj']:,} <= cap={prev} obj={prev_r['obj']:,}  "
          f"-> {'OK (15s resolved it)' if ok else 'STILL REGRESSION (15s insufficient)'}")
