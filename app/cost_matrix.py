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
import heapq
from typing import Any

try:
    from .delta_v import transfer_delta_v  # when imported as part of the app package
    from .removal_method import METHOD_MONITOR_ONLY
except ImportError:
    from delta_v import transfer_delta_v  # pyright: ignore[reportImplicitRelativeImport]
    from removal_method import METHOD_MONITOR_ONLY  # pyright: ignore[reportImplicitRelativeImport]

DEFAULT_POOL_SIZE = 40  # per handoff: ~30-50 candidates, not a forced top-5
DELTA_V_SCALE = 1000    # km/s -> integer units for OR-Tools (1 unit = 1 m/s of delta-v)

# Stage-1 prefilter slack multiplier for the two-stage reachability filter.
# We expand the direct depot->object cutoff by this factor so Stage 1 is
# guaranteed to be a SUPERSET of the true reachable set (never a subset).
# A factor of 3 means: include any object whose direct-hop cost is <=
# 3 * fuel_budget_km_s.  Objects that appear reachable via a multi-hop
# path at cost <= budget but whose direct hop is > 3*budget are
# essentially unreachable in practice (they'd need an intermediate that
# costs > 2*budget just to get there and back to the path), so this slack
# is both generous and safe.  Stage 2's real Dijkstra confirms the actual
# reachability over this bounded superset.
STAGE1_SLACK_MULTIPLIER = 3.0


def compute_reachable_costs(
    depot: dict[str, Any],
    objects: list[dict[str, Any]],
    matrix: list[list[float]],
) -> dict[int, float]:
    """Run Dijkstra from the depot (index 0) over a pre-built cost matrix to
    compute the TRUE shortest-path delta-v cost to every object in `objects`.

    The graph has N+1 nodes: index 0 is the depot, indices 1..N are the
    objects in `objects` order.  `matrix` must be the (N+1) x (N+1) cost
    matrix built by build_cost_matrix([depot] + objects).

    Returns a dict mapping norad_id -> shortest-path delta-v (km/s).
    Objects that are unreachable (no path within any finite budget) still
    appear in the dict -- the caller is responsible for filtering by budget.

    Delta-v costs are non-negative by construction (transfer_delta_v always
    returns a non-negative float), so Dijkstra is the correct algorithm here.
    It is independently unit-testable: given any synthetic depot, objects list,
    and matrix, the returned costs must satisfy:
      - costs[norad_id] <= direct_depot_cost   (never worse than direct hop)
      - costs[norad_id] == matrix[0][i]        when no multi-hop path is cheaper
    """
    n_nodes = len(matrix)  # depot + len(objects)
    dist = [float("inf")] * n_nodes
    dist[0] = 0.0
    # min-heap of (cost, node_index)
    heap: list[tuple[float, int]] = [(0.0, 0)]

    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue  # stale entry
        for v in range(n_nodes):
            if v == u:
                continue
            nd = d + matrix[u][v]
            if nd < dist[v]:
                dist[v] = nd
                heapq.heappush(heap, (nd, v))

    # Map object indices (1..N) back to norad_id.
    # depot is index 0 so object i maps to dist[i+1].
    result: dict[int, float] = {}
    for i, obj in enumerate(objects):
        result[obj["norad_id"]] = dist[i + 1]
    return result


def select_candidate_pool(
    scored_objects: list[dict[str, Any]],
    pool_size: int = DEFAULT_POOL_SIZE,
    depot: dict[str, Any] | None = None,
    fuel_budget_km_s: float | None = None,
) -> list[dict[str, Any]]:
    """Take the top `pool_size` objects by risk_score. score_debris_field()
    already returns its list sorted descending, but this re-sorts defensively
    so it's correct even if called on unsorted input.

    monitor_only objects are excluded before the sort/slice, not after:
    they mean "too small/scattered to realistically capture, real missions
    track these from the ground" -- they were never real visitable targets,
    so they shouldn't occupy a pool slot or be routable at all. Objects
    with no removal_method yet (e.g. this module's own __main__ test data,
    or any caller that hasn't run add_removal_methods()) pass through
    unaffected -- .get() returns None, which never equals the sentinel.

    When both `depot` and `fuel_budget_km_s` are provided, a TRUE shortest-
    path reachability filter is applied before the risk-sort/slice:

    Stage 1 (cheap prefilter): objects whose direct depot->object delta-v
      exceeds STAGE1_SLACK_MULTIPLIER * fuel_budget_km_s are dropped.  This
      bounds the size of the O(n^2) matrix build and Dijkstra run.  Stage 1
      is always a SUPERSET of the true reachable set -- it can only include
      more candidates than the correct answer, never exclude a reachable one.
      The invariant: any object reachable via a multi-hop path at cost <=
      budget must have at least one hop in that path with cost <=
      STAGE1_SLACK_MULTIPLIER * budget (otherwise the path would cost more
      than STAGE1_SLACK_MULTIPLIER * budget, making it unreachable by budget).

    Stage 2 (real Dijkstra reachability): build the full pairwise delta-v
      matrix over the Stage-1 superset plus the depot, run Dijkstra from the
      depot, and keep only objects whose shortest-path cost <= fuel_budget_km_s.
      This correctly identifies objects that are cheaper to reach via an
      intermediate hop than via a direct depot->object burn (e.g. plane changes
      at higher altitudes are cheaper -- a direct high-inclination hop might cost
      2.0 km/s, but going through a nearby mid-inclination object first might
      bring it down to 1.2 km/s, a saving that the Stage-1 filter can't see).

    Survivors of Stage 2 are risk-sorted and sliced to pool_size.

    When depot or fuel_budget_km_s is None (the default), exact current behavior
    applies: pure risk-sort, no reachability filter.  All existing callers that
    omit these parameters continue to work unmodified.
    """
    routable = [o for o in scored_objects if o.get("removal_method") != METHOD_MONITOR_ONLY]

    if depot is None or fuel_budget_km_s is None:
        # Default path: pure risk sort, backward-compatible with all existing callers.
        ordered = sorted(routable, key=lambda o: o.get("risk_score", 0.0), reverse=True)
        return ordered[:pool_size]

    # --- Two-stage reachability filter ---

    # Stage 1: cheap direct-hop prefilter to bound the matrix size.
    # Compute direct delta-v from depot to each object without building the
    # full matrix (single transfer_delta_v call per object, O(n)).
    cutoff = STAGE1_SLACK_MULTIPLIER * fuel_budget_km_s
    stage1: list[dict[str, Any]] = []
    for obj in routable:
        direct = transfer_delta_v(
            depot["altitude_km"], depot["inclination_deg"],
            obj["altitude_km"], obj["inclination_deg"],
            raan1_deg=depot.get("raan_deg", 0.0),
            raan2_deg=obj.get("raan_deg", 0.0),
        )["delta_v_total_km_s"]
        if direct <= cutoff:
            stage1.append(obj)

    if not stage1:
        # Nothing passed Stage 1: the fuel budget is tighter than the cheapest
        # possible direct hop * slack, so no object is reachable at all.
        return []

    # Stage 2: build the (N+1) x (N+1) matrix over [depot] + stage1 and run Dijkstra.
    nodes = [depot] + stage1
    matrix = build_cost_matrix(nodes)
    reachable_costs = compute_reachable_costs(depot, stage1, matrix)

    # Keep only objects whose shortest-path cost is within the fuel budget.
    reachable = [o for o in stage1 if reachable_costs.get(o["norad_id"], float("inf")) <= fuel_budget_km_s]

    # Risk-sort survivors and slice to pool_size.
    ordered = sorted(reachable, key=lambda o: o.get("risk_score", 0.0), reverse=True)
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
                # .get(..., 0.0): real debris objects carry raan_deg from
                # tle_fetch.py. The depot node (optimizer.py's
                # _build_depot_node) doesn't have one yet, so hops touching
                # the depot silently fall back to the old |incl1-incl2|
                # behavior until a start_raan_deg request field exists --
                # a safe fallback, not a crash, but still a known gap for
                # that one node specifically (see delta_v.py docstring).
                raan1_deg=a.get("raan_deg", 0.0),
                raan2_deg=b.get("raan_deg", 0.0),
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
                # raan_deg added: jittered per-cluster like inclination/altitude,
                # so the synthetic test data actually exercises the new RAAN
                # path instead of silently falling back to the 0.0 default
                # (which would defeat the point of this sanity check).
                "raan_deg": round(random.uniform(0.0, 360.0), 4),
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
