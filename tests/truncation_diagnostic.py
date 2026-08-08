"""
truncation_diagnostic.py — measures how often RAAN-drift truncation
actually fires on real data, and by how much, BEFORE building the
wait-time model. Read-only: does not modify any project files, does not
commit anything.

WHY MONKEYPATCH INSTEAD OF REIMPLEMENTING THE WALK:
The RAAN-drift post-solve walk lives entirely INSIDE optimize_route() in
optimizer.py and isn't exposed as a separate function or return value —
only the final (possibly-truncated) result comes out. Reimplementing that
walk in a separate script risks silently diverging from the real logic
over time. Instead, this script monkeypatches optimizer.py's own
`transfer_delta_v` name (the exact function optimize_route() calls
internally, once per leg, during the drift walk) with a wrapper that logs
every call and its inputs/outputs, then restores the original after.
This guarantees we're observing the REAL code path, not a copy of it.

Note: cost_matrix.py imports transfer_delta_v under its own separate
name binding (used earlier, to build the N-x-N solver cost matrix) — so
this patch does NOT affect that call site, only the post-solve drift
walk inside optimizer.py, which is what we want to instrument.

Run from inside the repo's `app/` directory, same venv/deps main.py uses:

    cd belka/app
    python /path/to/truncation_diagnostic.py

OUTPUT:
- How many of N runs had at least one truncation event.
- For every leg evaluated during a truncating run: whether it was the
  truncation point, and its overage (drifted_cost - fuel_remaining_at_
  that_point) in km/s and as a %.
- Summary stats on overage sizes, to judge whether a capped 3-5 day wait
  (which only claws back a few degrees of RAAN drift) could plausibly
  have closed that gap.
"""
import sys
import os
import statistics

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import optimizer  # noqa: E402  (import as module so we can monkeypatch its name binding)
from tle_fetch import get_debris_field  # noqa: E402
from risk_score import score_debris_field  # noqa: E402
from cost_matrix import select_candidate_pool, DEFAULT_POOL_SIZE  # noqa: E402

# ---- Grid to sweep. Adjust to match realistic demo-time request values. ----
FUEL_BUDGETS_KM_S = [3.0, 5.0, 8.0, 12.0]
START_ALTITUDE_KM = 800.0
START_INCLINATION_DEG = 74.0
START_RAAN_DEG = 0.0
POOL_SIZE = DEFAULT_POOL_SIZE
N_REPEATS_PER_BUDGET = 3  # cache/pool composition can shift slightly run to run


class DriftCallLogger:
    """Wraps optimizer.transfer_delta_v. Records every call made during
    a single optimize_route() invocation, then can be cleared between runs.
    Signature must exactly match delta_v.transfer_delta_v's kwargs since
    optimizer.py calls it with keyword arguments."""

    def __init__(self, real_fn):
        self._real_fn = real_fn
        self.calls = []

    def __call__(self, *args, **kwargs):
        result = self._real_fn(*args, **kwargs)
        self.calls.append({"kwargs": kwargs, "delta_v_total_km_s": result["delta_v_total_km_s"]})
        return result

    def reset(self):
        self.calls = []


def main():
    real_transfer_delta_v = optimizer.transfer_delta_v
    logger = DriftCallLogger(real_transfer_delta_v)
    optimizer.transfer_delta_v = logger  # monkeypatch: only affects optimizer.py's internal calls

    try:
        print("Fetching live debris field...")
        raw = get_debris_field()
        scored = score_debris_field(raw)

        n_runs = 0
        n_truncated_runs = 0
        truncation_overages_km_s = []
        truncation_overages_pct = []

        for fuel_budget in FUEL_BUDGETS_KM_S:
            for rep in range(N_REPEATS_PER_BUDGET):
                pool = select_candidate_pool(scored, pool_size=POOL_SIZE)

                logger.reset()
                result = optimizer.optimize_route(
                    pool=pool,
                    fuel_budget_km_s=fuel_budget,
                    start_altitude_km=START_ALTITUDE_KM,
                    start_inclination_deg=START_INCLINATION_DEG,
                    start_raan_deg=START_RAAN_DEG,
                )

                if "error" in result:
                    print(f"[budget={fuel_budget} rep={rep}] no feasible solution, skipping")
                    continue

                n_runs += 1
                visited_count = result["visited_count"]
                n_drift_calls = len(logger.calls)

                # The walk in optimizer.py breaks out of its loop on the
                # first leg whose drifted_cost exceeds fuel_remaining, i.e.
                # the last logged call BEFORE the break is the one that
                # exceeded budget -- unless the run completed with all
                # candidate legs affordable (no truncation).
                # We reconstruct fuel_remaining ourselves from the logged
                # sequence to identify that leg and its overage.
                fuel_remaining = fuel_budget
                truncated_this_run = False
                for i, call in enumerate(logger.calls):
                    dv = call["delta_v_total_km_s"]
                    if dv > fuel_remaining:
                        overage = dv - fuel_remaining
                        pct = (overage / fuel_remaining * 100) if fuel_remaining > 0 else float("inf")
                        truncation_overages_km_s.append(overage)
                        truncation_overages_pct.append(pct)
                        truncated_this_run = True
                        break
                    fuel_remaining -= dv

                if truncated_this_run:
                    n_truncated_runs += 1

                status = "TRUNCATED" if truncated_this_run else "completed clean"
                print(f"[budget={fuel_budget} rep={rep}] visited={visited_count}, "
                      f"drift-legs-evaluated={n_drift_calls}, {status}")

        print("\n" + "=" * 60)
        print(f"SUMMARY: {n_truncated_runs}/{n_runs} runs had a truncation event")
        print("=" * 60)

        if not truncation_overages_km_s:
            print("No truncation events observed in this sweep.")
            print("-> Wait-time model would have ZERO visible effect on this data/budget range.")
            print("-> Recommend NOT building it; keep the as-is truncation behavior.")
            return

        print(f"\n{len(truncation_overages_km_s)} truncation events recorded.")
        print(f"Overage (km/s)  — min: {min(truncation_overages_km_s):.5f}, "
              f"median: {statistics.median(truncation_overages_km_s):.5f}, "
              f"max: {max(truncation_overages_km_s):.5f}")
        print(f"Overage (%)     — min: {min(truncation_overages_pct):.2f}%, "
              f"median: {statistics.median(truncation_overages_pct):.2f}%, "
              f"max: {max(truncation_overages_pct):.2f}%")

        print("\nInterpretation guide:")
        print("- A capped 3-5 day wait realistically claws back only a few degrees of RAAN")
        print("  drift -- for most LEO orbits that's a SMALL delta-v saving, roughly")
        print("  low-hundreds of m/s at most, not km/s-scale.")
        print("- If most overages above are small (well under ~0.05-0.1 km/s), waiting")
        print("  could plausibly close some of these gaps -- worth building.")
        print("- If overages are consistently larger than that, waiting won't help and the")
        print("  feature would rarely, if ever, actually change a route's outcome.")

    finally:
        optimizer.transfer_delta_v = real_transfer_delta_v  # always restore, even on error


if __name__ == "__main__":
    main()
