"""
End-to-end pipeline test on REAL data. Run this after pushing tle_fetch.py's
fixed version and pulling it -- needs actual internet access to celestrak.org.

Chains everything built so far:
  get_debris_field()      -- Module A: real debris from Celestrak
  score_debris_field()    -- Module A: risk scoring
  select_candidate_pool() -- Module B step 2: top-N by risk
  optimize_route()        -- Module B step 3: orienteering solve

Edit FUEL_BUDGET_KM_S / START_ALTITUDE_KM / START_INCLINATION_DEG below to
match whatever scenario you want to test. There's no real "current spacecraft
orbit" concept yet -- these are placeholders until Module C wires in real
mission parameters.
"""
from app.tle_fetch import get_debris_field
from app.risk_score import score_debris_field
from app.cost_matrix import select_candidate_pool
from app.optimizer import optimize_route

# --- Tunable test scenario -------------------------------------------------
FUEL_BUDGET_KM_S = 2.2
START_ALTITUDE_KM = 800.0
START_INCLINATION_DEG = 74.0
POOL_SIZE = 150
# ----------------------------------------------------------------------------

print("Fetching real debris field from Celestrak...")
debris = get_debris_field()
print(f"  {len(debris)} real objects in the 700-1000km band")

print("Scoring risk...")
scored = score_debris_field(debris)
print("  Top 5 riskiest real objects:")
for o in scored[:5]:
    print(f"    {o['name']:<25} risk={o['risk_score']} alt={o['altitude_km']}km incl={o['inclination_deg']}deg")

pool = select_candidate_pool(scored, pool_size=POOL_SIZE)
print(f"\nCandidate pool: {len(pool)} objects (pool_size={POOL_SIZE})")

print(f"\nSolving route: budget={FUEL_BUDGET_KM_S}km/s, start=({START_ALTITUDE_KM}km, {START_INCLINATION_DEG}deg)")
result = optimize_route(
    pool,
    fuel_budget_km_s=FUEL_BUDGET_KM_S,
    start_altitude_km=START_ALTITUDE_KM,
    start_inclination_deg=START_INCLINATION_DEG,
)

if "error" in result:
    print(f"\n{result['error']}")
else:
    print(f"\nVisited {result['visited_count']}/{len(pool)} objects")
    print(f"Fuel used: {result['total_fuel_cost_km_s']} / {result['fuel_budget_km_s']} km/s ({result['fuel_used_fraction']*100:.1f}%)")
    print(f"Total risk collected: {result['total_risk_collected']}")
    print(f"\nRoute order: {result['route']}")
    print(f"\nSkipped ({result['skipped_count']}): {result['skipped_names']}")
    print("\nPer-step breakdown:")
    for step in result["step_breakdown"]:
        print(f"  {step['from']:<28} -> {step['to']:<15} {step['delta_v_km_s']:.4f} km/s")
