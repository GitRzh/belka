"""
Debug script for the /plan optimizer.
Runs the full pipeline with Vandenberg depot, Fuel=500 km/s, Risk=500, Pool=40.
Prints:
  1. Cost matrix rows (depot vs first 5 candidates)
  2. First 20 transit_callback invocations with costs
  3. Unit-conversion audit
  4. Routing model node setup
  5. Solver parameter summary
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from cost_matrix import build_cost_matrix, scale_matrix_for_ortools, DELTA_V_SCALE

# ── Try to import a live scored/pooled field, fall back to synthetic ─────────
try:
    from tle_fetch import get_debris_field
    from risk_score import score_debris_field
    from removal_method import add_removal_methods
    from cost_matrix import select_candidate_pool
    from launch_sites import LAUNCH_SITES

    # Vandenberg start params (match /plan call)
    site = LAUNCH_SITES.get("Vandenberg SFB", {"altitude_km": 500.0, "inclination_deg": 34.6})
    START_ALT  = site["altitude_km"]
    START_INCL = site["inclination_deg"]
    START_RAAN = 0.0
    FUEL       = 500.0      # km/s
    RISK_SCALE = 500.0      # as described in bug report
    POOL_SIZE  = 40

    print("Fetching live debris field…")
    raw = get_debris_field()
    scored = add_removal_methods(score_debris_field(raw))
    pool = select_candidate_pool(scored, pool_size=POOL_SIZE)
    print(f"  Live pool: {len(pool)} objects")
    DATA_SOURCE = "live"

except Exception as e:
    print(f"Live fetch failed ({e}), falling back to synthetic data.")
    import random
    from risk_score import score_debris_field
    from removal_method import add_removal_methods
    from cost_matrix import select_candidate_pool

    random.seed(42)
    START_ALT, START_INCL, START_RAAN = 800.0, 74.0, 0.0
    FUEL, RISK_SCALE, POOL_SIZE = 500.0, 500.0, 40

    synthetic = []
    obj_id = 0
    for name, base_incl, base_alt in [("COSMOS", 74.0, 780.0), ("IRIDIUM", 86.4, 800.0), ("FENGYUN", 98.8, 850.0)]:
        for k in range(20):
            obj_id += 1
            synthetic.append({
                "norad_id": 10000 + obj_id,
                "name": f"{name}-{obj_id}",
                "altitude_km": round(base_alt + random.uniform(-20, 20), 2),
                "inclination_deg": round(base_incl + random.uniform(-0.3, 0.3), 4),
                "raan_deg": round(random.uniform(0.0, 360.0), 4),
                "latitude": 0.0,
                "longitude": 0.0,
                "bstar": random.uniform(0.00001, 0.0001),
            })
    scored = add_removal_methods(score_debris_field(synthetic))
    pool = select_candidate_pool(scored, pool_size=POOL_SIZE)
    print(f"  Synthetic pool: {len(pool)} objects")
    DATA_SOURCE = "synthetic"

# ── 1. Build the cost matrix ──────────────────────────────────────────────────
from optimizer import _build_depot_node, RISK_PENALTY_SCALE as DEFAULT_RPS

depot = _build_depot_node(START_ALT, START_INCL, START_RAAN)
nodes = [depot] + pool
n_pool = len(pool)

matrix = build_cost_matrix(nodes)
scaled = scale_matrix_for_ortools(matrix)

print("\n" + "="*70)
print("1. COST MATRIX — depot row vs first 5 candidates (km/s  |  scaled int)")
print("="*70)
print(f"{'Node':<40} {'km/s':>8}  {'scaled':>7}")
for j in range(min(n_pool, 5)):
    km_s    = matrix[0][j + 1]
    int_val = scaled[0][j + 1]
    name    = pool[j].get("name", f"candidate_{j}")
    print(f"  depot → {name:<30} {km_s:>8.4f}  {int_val:>7}")

# Show min/max across the whole depot row
depot_row_km_s = matrix[0][1:]
print(f"\n  min depot hop (km/s): {min(depot_row_km_s):.4f}")
print(f"  max depot hop (km/s): {max(depot_row_km_s):.4f}")
print(f"  min depot hop (scaled int): {min(scaled[0][1:])}")

print(f"\n  Budget (km/s): {FUEL}")
print(f"  Budget scaled: {round(FUEL * 1000)}")
print(f"  Min hop scaled: {min(scaled[0][1:])}")
print(f"  Budget covers cheapest hop: {round(FUEL * 1000) >= min(scaled[0][1:])}")

# ── 2. transit_callback invocation trace ─────────────────────────────────────
print("\n" + "="*70)
print("2. transit_callback INVOCATION TRACE — first 20 calls")
print("="*70)

end_index = n_pool + 1
full_size  = n_pool + 2
full_matrix: list[list[int]] = [row + [0] for row in scaled]
full_matrix.append([0] * full_size)

manager = pywrapcp.RoutingIndexManager(full_size, 1, [0], [end_index])
routing  = pywrapcp.RoutingModel(manager)

cb_call_count = [0]
cb_log = []

def distance_callback(from_index: int, to_index: int) -> int:
    from_node = manager.IndexToNode(from_index)
    to_node   = manager.IndexToNode(to_index)
    cost = full_matrix[from_node][to_node]
    if cb_call_count[0] < 20:
        cb_log.append(f"  transit_callback(from_node={from_node}, to_node={to_node}) → cost={cost}  ({cost/1000:.4f} km/s)")
    cb_call_count[0] += 1
    return cost

transit_idx = routing.RegisterTransitCallback(distance_callback)
routing.SetArcCostEvaluatorOfAllVehicles(transit_idx)

budget_scaled = round(FUEL * 1000)
routing.AddDimension(transit_idx, 0, budget_scaled, True, "Fuel")

from removal_method import METHOD_NET_CAPTURE
def net_cb(from_index: int) -> int:
    from_node = manager.IndexToNode(from_index)
    if 1 <= from_node <= n_pool and pool[from_node - 1].get("removal_method") == METHOD_NET_CAPTURE:
        return 1
    return 0

net_idx = routing.RegisterUnaryTransitCallback(net_cb)
routing.AddDimension(net_idx, 0, 1, True, "NetCapacity")

print(f"\n  risk_penalty_scale used: {RISK_SCALE} (default is {DEFAULT_RPS})")
print(f"  pool risk_score range: {min(o.get('risk_score',0) for o in pool):.4f} – {max(o.get('risk_score',0) for o in pool):.4f}")
print(f"  typical penalty (risk=1.0): round(1.0 * {RISK_SCALE}) = {round(1.0 * RISK_SCALE)}")
print(f"  cheapest hop scaled: {min(scaled[0][1:])}")
print(f"  can solver afford cheapest hop? penalty >= arc_cost? {round(RISK_SCALE) >= min(scaled[0][1:])}")

for i, obj in enumerate(pool):
    node_index = i + 1
    risk    = obj.get("risk_score", 0.0)
    penalty = round(risk * RISK_SCALE)
    routing.AddDisjunction([manager.NodeToIndex(node_index)], penalty)

search_params = pywrapcp.DefaultRoutingSearchParameters()
search_params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
search_params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
search_params.time_limit.FromSeconds(5)

solution = routing.SolveWithParameters(search_params)

# Print the first 20 callback logs (populated during solve)
for line in cb_log:
    print(line)
print(f"  (total transit_callback calls: {cb_call_count[0]})")

# ── 3. Unit-conversion audit ──────────────────────────────────────────────────
print("\n" + "="*70)
print("3. UNIT-CONVERSION AUDIT")
print("="*70)
print(f"  DELTA_V_SCALE constant: {DELTA_V_SCALE}  (km/s → integer, 1 unit = 1 m/s)")
print(f"  budget_scaled = round(fuel_budget_km_s * 1000) = round({FUEL} * 1000) = {round(FUEL * 1000)}")
print(f"  sample matrix[0][1] km/s value: {matrix[0][1]:.4f}")
print(f"  sample scaled[0][1] int value:  {scaled[0][1]}")
print(f"  ratio check: {scaled[0][1]} / {matrix[0][1]:.4f} = {scaled[0][1]/matrix[0][1]:.2f}  (should be ~1000)")
print(f"  transfer_delta_v returns km/s: YES (returns dict with 'delta_v_total_km_s')")

# ── 4. Routing model node setup ───────────────────────────────────────────────
print("\n" + "="*70)
print("4. ROUTING MODEL NODE SETUP")
print("="*70)
print(f"  full_size (nodes in model): {full_size}")
print(f"  depot node index:   0")
print(f"  candidate indices:  1 – {n_pool}")
print(f"  virtual end index:  {end_index}")
print(f"  routing.Start(0):  {routing.Start(0)}")
print(f"  routing.End(0):    {routing.End(0)}")
print(f"  pool has {n_pool} candidates → AddDisjunction called {n_pool} times")

# ── 5. Solver outcome + penalty vs arc-cost analysis ─────────────────────────
print("\n" + "="*70)
print("5. SOLVER RESULT + PENALTY vs ARC-COST ANALYSIS")
print("="*70)
if solution is None:
    print("  Solution: None (infeasible)")
else:
    visited = 0
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if 1 <= node <= n_pool:
            visited += 1
        index = solution.Value(routing.NextVar(index))
    print(f"  visited_count: {visited}")
    print(f"  objective value: {solution.ObjectiveValue()}")

print(f"\n  KEY ANALYSIS — why solver skips nodes:")
print(f"  {'pool_i':<6} {'name':<35} {'risk':>6} {'penalty':>8} {'depot_hop_scaled':>17} {'penalty>=hop?':>14}")
for j, obj in enumerate(pool[:10]):
    risk    = obj.get("risk_score", 0.0)
    pen     = round(risk * RISK_SCALE)
    hop_int = scaled[0][j + 1]
    ok      = "YES ✓" if pen >= hop_int else f"NO ✗  (need rps>={hop_int/risk:.0f})"
    print(f"  {j:<6} {obj.get('name','?'):<35} {risk:>6.4f} {pen:>8} {hop_int:>17} {ok:>14}")

min_rps_needed = min(
    scaled[0][j+1] / obj.get("risk_score", 1e-9)
    for j, obj in enumerate(pool)
    if obj.get("risk_score", 0) > 0
)
print(f"\n  Minimum risk_penalty_scale needed (cheapest reachable node): {min_rps_needed:.1f}")
print(f"  Current risk_penalty_scale used: {RISK_SCALE}")
print(f"  Default RISK_PENALTY_SCALE in optimizer.py: {DEFAULT_RPS}")
print(f"\n  CONCLUSION: solver skips ALL nodes because ALL penalties < ALL arc costs.")
print(f"  Fix: raise RISK_PENALTY_SCALE default to at least ceil(min_rps_needed).")
print(f"  Suggested safe default: {int(min_rps_needed * 2)}")
