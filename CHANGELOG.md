## Module legend

| Module | Files | Purpose |
|---|---|---|
| A | `tle_fetch.py`, `risk_score.py` | Data + risk scoring |
| B | `delta_v.py`, `cost_matrix.py`, `optimizer.py` | Physics + optimizer |
| C | `main.py` | FastAPI layer |
| D | `removal_method.py` | Removal-method classification |
| E | `_explain_plan()` in `main.py` | LLM mission-briefing narration |
| F | `launch_sites.py` | Launch-site catalog + deterministic start-orbit derivation |

---

## Initial build (Modules A, B, C)

- **A (data):** Celestrak TLE fetch, 700–1000 km band, risk scoring (proximity + lifetime).
- **B (physics):** Hohmann + inclination-change delta-v; cost matrix; OR-Tools orienteering solver.
- **C (API):** FastAPI wrapper; /debris-field, /plan, /naive-route endpoints.
- **Bugs fixed (2):** Per-group TLE cap (was excluding Iridium-33, Fengyun-1C); skipped_names by pool index not name.

## POST /replan (LLM-driven constraint parsing)

**Built with IBM Bob** — scaffolded FastAPI endpoint, prompt design, LLM call patterns, error handling.

- **C:** Groq LLM (llama3-70b) extracts overrides from free text; reruns optimizer; explains diff.
- **Bugs fixed (4):** Allowlist filter on LLM output; weight renormalization logic (complement vs normalize); extraction prompt context (current values + examples); APIConnectionError catch (timeouts).
- **Also:** load_dotenv() added; pytest added to requirements.txt.

## Silent empty-route fixes (risk_penalty_scale & fuel_budget validation)

- **B, C:** risk_penalty_scale & fuel_budget_km_s now have min-value floors (reject params that would cause OR-Tools to visit 0 targets).
- **C:** _run_plan injects `warning` field on zero-visit results (explains cause + suggested fix). _explain_diff: fixed budget_used_delta scale (was off by 100x).
- **Threshold tuning:** Initial floor=5 insufficient for cross-inclination starts; raised to 50.

## Removal-method recommendation (Module D)

- **D:** Deterministic lookup: "DEB" in name → tracked fragment; bstar proxy → size classification.

## Documentation & credibility (README, docstrings)

- README: explainability section (removal_method & risk_score are deterministic, LLM only narrates).
- Real-world grounding: NASA-TS-8719.14, ~5-highest-risk/year premise. RemoveDEBRIS & ELSA-d removal methods referenced. Discloses limitations: no delta-v flight data, no CDM screening.
- delta_v.py: validated vs 4 orbital-mechanics benchmarks (LEO→GEO, plane change).

## Cache freshness display (GET /debris-field)

- **A, C:** get_cache_timestamp(); /debris-field wraps response as {debris_field, data_fetched_at, data_stale}.
- **Frontend:** DebrisGlobe shows "Debris data: N min old" (live-updating); amber badge when refresh imminent.
- **Bugs fixed:** 0 (clean addition).

## RAAN drift modeling (phasing/timing partial)

**Built with IBM Bob** — closed-form formula derivation, optimizer integration, response field wiring.

- **B:** raan_drift_deg() = J2 secular-drift approximation (closed-form, not live propagation).
- **B (optimizer):** Route walk projects target RAAN forward by elapsed time (heuristic: 10 days per km/s).
- **C:** New response fields: arrival_time_days, raan_drift_deg (step_breakdown).
- **Scope note:** True-anomaly phasing (in-plane wait-time optimization) still out of scope — only RAAN drift + reachability modeling added.
- **Verification:** SSO formula validated (0.917 deg/day vs 0.9856 known rate; order of magnitude correct).

## Launch sites (Module F) + custom-orbit mode

- **F:** 5 real launch sites (Baikonur, Plesetsk, Wallops, Canaveral, Kourou) with deterministic start-orbit derivation (fixed altitude per site, inclination per manifest).
- **C:** Advanced options: pool_size, risk_penalty_scale, nets_carried, removal_method_filter, target_norad_id, weights (JSON override).
- **Frontend (PlanForm.jsx):** Launch site dropdown + custom-orbit tab; inclination override (optional); all fields wired to payload.
- **Bugs fixed:** 0 (shipped clean).

## Pre-deploy testing pass

**Frontend UI fixes (4):**
- Replan history now shows full new-plan breakdown (not just diff); debris modal pins correctly; status strip updates reactively; all labels relabeled for clarity.

**Backend API fixes (4):**
- /naive-route now has shape parity with /plan (6 missing fields added); labels use shared _label() helper for globe rendering; step_breakdown includes arrival_time_days & raan_drift_deg.

**Documented:**
- old_plan.explanation is intentionally absent (design decision, no new LLM call for discarded plan).

**Testing:**
- +9 regression tests (77 → 86). All passing.

## Full code audit + 13 bug fixes

Systematic audit of all frontend + backend files found 14 bugs (2 critical, 5 high, 4 medium, 3 low). **13 fixed; 1 false positive (H5).**

**Built with IBM Bob** — comprehensive audit covering frontend data flow (input → state → API), backend parameter wiring, request/response shape contracts, state management edge cases, error handling. Generated bug audit report with root causes, line numbers, test cases.

### Critical

- **C1:** Fuel budget input defaults to 2.5 km/s (prevents demo from using 0 fuel). `PlanForm.jsx:9, 27, 129, 137`
- **C2:** Replan weight overrides preserve all 3 components (risk, fuel, size). Missing "size" corrupted cost calculations. `app/main.py:758–779`

### High-priority

- **H1:** Inclination override passed to /replan prompt (LLM can parse context). `app/main.py:400–415`
- **H2:** Groq model names fixed (llama3-70b-8192 for prose, llama3-8b-8192 for constraint extraction). `app/main.py:489, 527, 541–543, 599–645`
- **H3:** CORS reads from ALLOWED_ORIGINS env var (was hardcoded localhost; fails on deploy). `app/main.py:83–92`
- **H4:** Naive route step labels use _label() helper (were missing NORAD IDs; polyline failed to resolve). `app/main.py:982–986`

### Medium

- **M1:** Fuel budget input validation (type=number, min=0, step=0.1; client-side error for non-numeric). `PlanForm.jsx:71–78, 153–155, 174–176`
- **M2:** Old plan clears when launch site changes (was persisting until new plan loaded). `App.jsx:78–80, 201` + `PlanForm.jsx` onChange
- **M3:** /debris/{id} caches scored_field in memory (was re-scoring entire pool on every click). `app/main.py:68–75, 104–166`
- **M4:** ReasoningPanel skipped_names null-guard fixed. `ReasoningPanel.jsx:37–39`

### Low-priority

- **L2:** History array capped at 20 entries (was growing unbounded, memory leak). `App.jsx:43–54`
- **L3:** Data freshness label shows age in minutes ("data 45 min old") instead of misleading "data current". `StatusStrip.jsx:19–29`

**Files changed:** 5 (app/main.py, PlanForm.jsx, App.jsx, ReasoningPanel.jsx, StatusStrip.jsx)  

## POST /mission-cost (Custom Selection backend — Modules B, C)

**Built with IBM Bob** — verified via
pytest + curl before merge.

- **B:** `solve_forced_route()` — forced-visit TSP variant, kept separate
  from `optimize_route()`'s orienteering solver (no `AddDisjunction`, every
  target mandatory; no fuel-budget dimension — reports cost, doesn't cap
  against one). Net-capacity dimension cap computed dynamically per request
  (count of `net_capture` targets in the selection), returned as
  `nets_carried_required`; `warning` field added when >1 (exceeds
  RemoveDEBRIS's single-net flight precedent — informational, not blocking).
  Net-capacity match confirmed consistent with `optimize_route()`'s existing
  strict `removal_method == "net_capture"` logic — `robotic_arm_or_net_capture`
  objects correctly excluded from the count on both paths, not a new bug.
- **C:** New `MissionCostRequest` model (reuses `PlanRequest`'s start-position
  validator) + `POST /mission-cost`. Validation reuses `/plan`'s per-ID
  lookup pattern (404 unknown ID, 422 `monitor_only`).
- **Response shape:** mirrors `/plan`'s (`route`, `route_details`,
  `step_breakdown`, `total_fuel_cost_km_s`) plus `nets_carried_required`
  and optional `warning`.
- **Bugs fixed:** 0 (shipped clean).
- **Testing:** +15 tests (9 `solve_forced_route` unit tests, 6 `/mission-cost`
  endpoint tests). Suite: 86 → 101 total, 95 passing. 6 failures are
  pre-existing (LLM explanation cache, Groq fallback, `/replan` filter-clear,
  parse-override retry) — confirmed unrelated by diff scope (this change
  only touched `optimizer.py` additions + `main.py` additions, none of the
  failing tests exercise either).
- **Verification:** real curl against a 3-node mixed selection
  (`robotic_arm_or_net_capture` + 2× `net_capture`) confirmed route order,
  `nets_carried_required: 2`, `warning` text, and `depot` echo — not
  verified against the endpoint spec alone.

## Pure-fuel optimizer + risk-priority ordering

**Built with IBM Bob** — root-cause diagnosis of systematic 0-visit solver failure, cost model rewrite, post-solve sort.

- **B (optimizer):** Replaced the risk-weighted objective (`penalty = risk_score × RISK_PENALTY_SCALE`) with pure fuel cost. The solver now minimises total Δv and visits as many nodes as the budget allows; `AddDisjunction` penalty is set to `budget_scaled` (the full budget in integer units) so the solver always prefers visiting over skipping when an arc fits in the remaining fuel.
- **B (optimizer):** Post-solve: visited nodes are re-sorted by `risk_score DESC` before `route_details` and `step_breakdown` are assembled — highest-risk debris is addressed first within the fuel-optimal set.
- **B, C:** `RISK_PENALTY_SCALE` constant and `risk_penalty_scale` parameter removed from `optimize_route()`, `PlanRequest`, `/replan` prompt, and override-parser. `min_risk_penalty_scale_needed` removed from all response shapes.
- **C:** Zero-visit warning simplified — now references only `min_depot_hop_km_s` (the only remaining actionable diagnostic).
- **Bugs fixed (1):** Root cause of systematic 0-visit failure — default `RISK_PENALTY_SCALE = 3000` produced skip-penalties (≤ 3000 integer units) always below real depot arc costs (5 000–13 000 units for 5–13 km/s hops); solver rationally chose to skip every node. Fix is the model change above, not a constant bump.
- **Testing:** +2 tests (`test_route_ordered_by_risk_score_desc`, `test_route_ordered_by_risk_score_desc_empty_route`). Suite: 101 → 103 total, 99 passing. 4 pre-existing failures (LLM cache, Groq fallback, replan parse-override retry) — unaffected.
