"""
One-time measurement script: elapsed time for each of the 4 fix_type dry-run paths.

Scenario: Baikonur (start_altitude_km=200, start_inclination_deg=51.6),
          fuel_budget_km_s=2.5  →  visits 0 objects on first run (failed plan).

Each section re-runs _run_plan() with the modified parameters and prints
perf_counter elapsed time. No mocking — real optimizer, real cached debris pool.

Run from repo root:
    python -m tests.measure_fix_type_cost
"""
import sys
import os
import time

# Ensure the repo root is on sys.path so `app` is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import PlanRequest, _run_plan, _get_scored_field
from app.cost_matrix import DEFAULT_POOL_SIZE
import app.tle_fetch as tle_fetch

# ---------------------------------------------------------------------------
# Warm up: pre-populate the in-memory cache exactly as a real /plan would,
# so timings below reflect optimizer cost only, not the first-fetch penalty.
# ---------------------------------------------------------------------------

print("=" * 70)
print("WARM-UP: loading debris field into _scored_field_cache ...")
t0 = time.perf_counter()
_get_scored_field()   # fills _scored_field_cache with default weights
warm_elapsed = time.perf_counter() - t0
print(f"  Warm-up done in {warm_elapsed:.4f}s")
print(f"  TLE cache file: {tle_fetch.CACHE_FILE}")
if os.path.exists(tle_fetch.CACHE_FILE):
    age_s = time.time() - os.path.getmtime(tle_fetch.CACHE_FILE)
    print(f"  TLE cache age: {age_s/60:.1f} min")
print()

# ---------------------------------------------------------------------------
# Baseline: confirm the scenario actually visits 0 objects
# ---------------------------------------------------------------------------

BASE = dict(
    start_altitude_km=200.0,    # Baikonur-ish depot altitude
    start_inclination_deg=51.6, # Baikonur inclination
    fuel_budget_km_s=2.5,
    pool_size=DEFAULT_POOL_SIZE,
)

print("BASELINE: Baikonur / 2.5 km/s budget / default pool")
baseline_req = PlanRequest(**BASE)
t0 = time.perf_counter()
baseline = _run_plan(baseline_req)
baseline_elapsed = time.perf_counter() - t0
print(f"  visited_count    = {baseline['visited_count']}")
print(f"  min_depot_hop    = {baseline.get('min_depot_hop_km_s', 'n/a')} km/s")
print(f"  pool_size_used   = {baseline.get('pool_size_used', 'n/a')}")
print(f"  elapsed          = {baseline_elapsed:.4f}s")
print()

# ---------------------------------------------------------------------------
# FIX TYPE 1: budget_increase
# Re-run optimizer with fuel_budget_km_s raised above min_depot_hop.
# All data already in _scored_field_cache → pure optimizer cost.
# ---------------------------------------------------------------------------

print("-" * 70)
print("FIX TYPE: budget_increase  (new_budget = 5.0 km/s)")
budget_req = PlanRequest(**{**BASE, "fuel_budget_km_s": 5.0})
t0 = time.perf_counter()
budget_result = _run_plan(budget_req)
budget_elapsed = time.perf_counter() - t0
print(f"  visited_count    = {budget_result['visited_count']}")
print(f"  total_fuel_cost  = {budget_result.get('total_fuel_cost_km_s', 'n/a'):.4f} km/s")
print(f"  pool_size_used   = {budget_result.get('pool_size_used', 'n/a')}")
print(f"  elapsed          = {budget_elapsed:.4f}s")
print()

# ---------------------------------------------------------------------------
# FIX TYPE 2: method_filter_change
# Re-run with removal_method_filter='net_capture'. Filters already-loaded
# scored list in _run_plan(); no re-fetch, no re-score.
# ---------------------------------------------------------------------------

print("-" * 70)
print("FIX TYPE: method_filter_change  (removal_method_filter = 'net_capture', budget = 5.0)")
method_req = PlanRequest(**{
    **BASE,
    "fuel_budget_km_s": 5.0,       # keep budget workable so we see non-zero result
    "removal_method_filter": "net_capture",
})
t0 = time.perf_counter()
method_result = _run_plan(method_req)
method_elapsed = time.perf_counter() - t0
print(f"  visited_count    = {method_result['visited_count']}")
print(f"  total_fuel_cost  = {method_result.get('total_fuel_cost_km_s', 'n/a'):.4f} km/s")
print(f"  pool_size_used   = {method_result.get('pool_size_used', 'n/a')}")
print(f"  elapsed          = {method_elapsed:.4f}s")
print()

# ---------------------------------------------------------------------------
# FIX TYPE 3: pool_size_increase
# Re-run with a larger pool. select_candidate_pool() slices more objects out
# of the already-scored list in _scored_field_cache. No re-fetch, no re-score.
# The optimizer cost grows O(n^2) for the cost-matrix build.
# ---------------------------------------------------------------------------

NEW_POOL = 120   # from 40 → 120 (3×)

print("-" * 70)
print(f"FIX TYPE: pool_size_increase  (pool_size 40 → {NEW_POOL}, budget = 5.0)")
pool_req = PlanRequest(**{
    **BASE,
    "fuel_budget_km_s": 5.0,
    "pool_size": NEW_POOL,
})
t0 = time.perf_counter()
pool_result = _run_plan(pool_req)
pool_elapsed = time.perf_counter() - t0
print(f"  visited_count    = {pool_result['visited_count']}")
print(f"  total_fuel_cost  = {pool_result.get('total_fuel_cost_km_s', 'n/a'):.4f} km/s")
print(f"  pool_size_used   = {pool_result.get('pool_size_used', 'n/a')}")
print(f"  elapsed          = {pool_elapsed:.4f}s")
print()

# ---------------------------------------------------------------------------
# FIX TYPE 4: altitude_expand
# _propose_fixes emits altitude_km for a new depot altitude. There is no
# start_altitude_km override key in _execute_overrides, so the proposal
# params would need to map to start_altitude_km on PlanRequest. Here we
# test the cost of re-running _run_plan with a different start_altitude_km
# (moving the depot to ~800 km, inside the debris band).
# All objects are already in _scored_field_cache; the altitude change only
# affects the depot→first-debris hop cost in the optimizer, NOT the debris
# dataset itself. No re-fetch required.
# ---------------------------------------------------------------------------

NEW_ALT = 800.0  # move depot into the 700-1000 km debris band

print("-" * 70)
print(f"FIX TYPE: altitude_expand  (start_altitude_km 200 → {NEW_ALT}, budget = 2.5)")
alt_req = PlanRequest(**{
    **BASE,
    "start_altitude_km": NEW_ALT,
})
t0 = time.perf_counter()
alt_result = _run_plan(alt_req)
alt_elapsed = time.perf_counter() - t0
print(f"  visited_count    = {alt_result['visited_count']}")
print(f"  total_fuel_cost  = {alt_result.get('total_fuel_cost_km_s', 'n/a'):.4f} km/s")
print(f"  pool_size_used   = {alt_result.get('pool_size_used', 'n/a')}")
print(f"  elapsed          = {alt_elapsed:.4f}s")
print()

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("=" * 70)
print("SUMMARY (elapsed times)")
print(f"  warm-up (_get_scored_field + _run_plan cache population):  {warm_elapsed:.4f}s")
print(f"  baseline         (2.5 km/s, pool=40):         {baseline_elapsed:.4f}s")
print(f"  budget_increase  (5.0 km/s, pool=40):         {budget_elapsed:.4f}s")
print(f"  method_filter    (net_capture, 5.0 km/s):     {method_elapsed:.4f}s")
print(f"  pool_size_increase (pool=120, 5.0 km/s):      {pool_elapsed:.4f}s")
print(f"  altitude_expand  (alt=800 km, 2.5 km/s):      {alt_elapsed:.4f}s")
print()
print("DATA PATH NOTE:")
print("  altitude_expand: debris pool is unchanged — same _scored_field_cache hit.")
print("                   Only the depot→debris hop cost changes in the optimizer.")
print("  pool_size_increase: slices deeper into the already-cached scored list.")
print("                   No re-fetch, no re-score. Optimizer matrix is larger → slower.")
print("=" * 70)
