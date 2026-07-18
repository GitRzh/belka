"""
Module B, step 2: N x N delta-v cost matrix.

Takes the candidate pool -- the top ~30-50 objects by risk_score from
score_debris_field(), NOT a forced top-5 -- and builds a full N x N delta-v
matrix using transfer_delta_v() from delta_v.py. This is the input the
orienteering solver (step 3) runs over: it's free to visit any subset of
this pool, in any order, as long as it stays within the fuel budget.

Two representations are produced:
- A float km/s matrix -- the real numbers, useful for debugging/display.
- An integer, OR-Tools-ready matrix -- RoutingModel arc-cost callbacks
  require integers, so this scales km/s up (DELTA_V_SCALE) and rounds.
  Built now so step 3 is purely wiring, not more math.
"""
from typing import Any

try:
    from .delta_v import transfer_delta_v  # when imported as part of the app package
except ImportError:
    from delta_v import transfer_delta_v  # pyright: ignore[reportImplicitRelativeImport]

DEFAULT_POOL_SIZE = 40  # per handoff: ~30-50 candidates, not a forced top-5
DELTA_V_SCALE = 1000    # km/s -> integer units for OR-Tools (1 unit = 1 m/s of delta-v)


def select_candidate_pool(
    scored_objects: list[dict[str, Any]],
    pool_size: int = DEFAULT_POOL_SIZE,
) -> list[dict[str, Any]]:
    """Take the top `pool_size` objects by risk_score. score_debris_field()
    already returns its list sorted descending, but this re-sorts defensively
    so it's correct even if called on unsorted input."""
    ordered = sorted(scored_objects, key=lambda o: o.get("risk_score", 0.0), reverse=True)
    return ordered[:pool_size]


def build_cost_matrix(objects: list[dict[str, Any]]) -> list[list[float]]:
    """N x N delta-v matrix in km/s. matrix[i][j] = cost of the maneuver
    between objects[i]'s orbit and objects[j]'s orbit. Symmetric (the
    physics doesn't care which direction you travel) with a zero diagonal.
    O(n^2) transfer_delta_v calls -- trivially fast at pool_size ~40-50."""
    n = len(objects)
    matrix = [[0.0] * n for _ in range(n)]

    for i in range(n):
        for j in range(i + 1, n):
            a, b = objects[i], objects[j]
            result = transfer_delta_v(
                a["altitude_km"], a["inclination_deg"],
                b["altitude_km"], b["inclination_deg"],
            )
            cost = result["delta_v_total_km_s"]
            matrix[i][j] = cost
            matrix[j][i] = cost

    return matrix


def scale_matrix_for_ortools(matrix: list[list[float]]) -> list[list[int]]:
    """OR-Tools routing arc costs must be integers. Scales km/s up by
    DELTA_V_SCALE and rounds -- 1 integer unit = 1 m/s of delta-v, which
    gives plenty of resolution since real debris-hop costs run from
    ~0.01 km/s (same-cluster) to 3+ km/s (cross-inclination), i.e.
    10 to 3000+ integer units. Rounding error at that resolution won't
    flip routing decisions."""
    return [[round(cost * DELTA_V_SCALE) for cost in row] for row in matrix]


if __name__ == "__main__":
    # Sanity/integration test. Celestrak isn't reachable from this sandbox
    # (network allowlist doesn't include it), so this uses a synthetic pool
    # shaped like the real thing: the three actual debris-cloud inclinations
    # from tle_fetch.py's DEBRIS_GROUPS, with realistic jitter --
    #   cosmos-2251-debris  ~74.0 deg
    #   iridium-33-debris   ~86.4 deg
    #   fengyun-1c-debris   ~98.8 deg
    # Swap this block for the real fetch+score pipeline once run against
    # live data: `score_debris_field(get_debris_field())`.
    import random

    from risk_score import score_debris_field  # pyright: ignore[reportImplicitRelativeImport]

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

    print(f"Synthetic debris field: {len(synthetic)} objects across 3 clusters (45 total)")

    scored = score_debris_field(synthetic)
    pool = select_candidate_pool(scored, pool_size=DEFAULT_POOL_SIZE)
    print(f"Candidate pool selected: {len(pool)} objects (pool_size={DEFAULT_POOL_SIZE}, so this should keep all 45 minus whatever pool_size trims)")

    matrix = build_cost_matrix(pool)
    n = len(matrix)
    print(f"\nCost matrix shape: {n} x {n}")

    # Structural checks
    diag_ok = all(matrix[i][i] == 0.0 for i in range(n))
    sym_ok = all(matrix[i][j] == matrix[j][i] for i in range(n) for j in range(n))
    print(f"Diagonal all zero: {diag_ok}")
    print(f"Matrix symmetric: {sym_ok}")

    all_costs = [matrix[i][j] for i in range(n) for j in range(n) if i != j]
    print(f"Cost range: {min(all_costs):.4f} - {max(all_costs):.4f} km/s")

    # Physical sanity check: same-cluster pairs should be cheap, cross-cluster expensive
    same_cluster_costs = []
    cross_cluster_costs = []
    for i in range(n):
        for j in range(i + 1, n):
            same = pool[i]["name"].split("-")[0] == pool[j]["name"].split("-")[0]
            (same_cluster_costs if same else cross_cluster_costs).append(matrix[i][j])

    print(f"\nAvg same-cluster hop:  {sum(same_cluster_costs)/len(same_cluster_costs):.4f} km/s  (should be small)")
    print(f"Avg cross-cluster hop: {sum(cross_cluster_costs)/len(cross_cluster_costs):.4f} km/s  (should be much larger)")

    scaled = scale_matrix_for_ortools(matrix)
    print(f"\nScaled (OR-Tools int) matrix sample, row 0: {scaled[0][:8]}...")
    print(f"Scale factor: {DELTA_V_SCALE} units per km/s (1 unit = 1 m/s)")
