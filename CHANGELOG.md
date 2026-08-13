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

## Removal Method Expert System (GET /debris/{id}/removal-methods)

- **C:** New endpoint generates per-object LLM reasoning grounded only in real TLE signals (BSTAR, altitude, inclination, risk_score); single Groq call (`openai/gpt-oss-20b`, distinct from `120b` used by route briefings); result cached in `_reasoning_cache` by `norad_id`; graceful degradation returns `reasoning_unavailable: true` on failure (never 500).
- **C:** Post-parse alternatives filter validates LLM output against a fixed allowlist (`net_capture`, `robotic_arm`, `monitor_only`); hallucinated method names silently dropped.
- **Frontend:** `DebrisInfoModal` gains a two-tab row (Info / Reason); Reason tab visible and default only when the clicked object is in the active plan's route target list; client-side reasoning cache via `useRef(Map)` prevents refetch on tab switch; NORAD ID shown as first field in Reason tab.
- **Frontend:** `activeRouteNoradIds` derived from `activePlan.route_details` in `App.jsx` and passed to `DebrisInfoModal`.
- **Testing:** +5 tests (`test_response_shape`, `test_content_safety_no_invented_mass_or_material`, `test_cache_prevents_second_llm_call`, `test_groq_failure_returns_200_not_500`, `test_alternatives_only_contains_known_methods`).

## One-click constraint-resolution proposal application (zero-LLM replan shortcut)

**Built with IBM Bob.**

- **C:** `_translate_proposal_params()` added — translates the fix-type-specific proposal shape (`{"fix_type": "budget_increase", "new_budget": 7.5}`) to canonical `_execute_overrides` keys before validation runs. Driven by a single `_PROPOSAL_PARAM_TO_OVERRIDE` dict defined once near `_FIX_TYPE_PARAMS_KEY`; `_build_dry_run_req()` refactored to use the same dict so the dry-run and apply paths share one source of truth and cannot drift.
- **C (fix):** `_execute_overrides()` previously no-op'd silently on every proposal-shortcut replan — `overrides_applied` was always `{}` because proposal params never matched the canonical key checks. Translation step inserted at the top of the function resolves this; the apply path now produces a real re-optimised plan with changed parameters.
- **Frontend (fix):** `handleApplyProposal` was sending `applied_proposal: proposal.params` (no `fix_type`); corrected to `{ ...proposal.params, fix_type: proposal.fix_type }` to match the field's own docstring and supply the key the backend needs for translation.
- **Frontend:** After any replan, `overrides_applied` is merged back into the stored `params` entry and `naivePlan` is cleared — Naive Route now re-runs against the same effective parameters as the AI route (e.g. the raised budget after a `budget_increase` fix) so both views describe the same mission.
- **Testing:** +7 regression tests (`TestRealProposalShapeRegression`) covering all four fix-types in real proposal shape, backwards-compat with pre-translated canonical dicts, and a guard that `fix_type` is stripped before reaching `PlanRequest`. Suite: 208 total, 205 passing (3 pre-existing failures unaffected).

## J2 drift wait-window wiring + fuel-saved reporting

**Built with IBM Bob** — end-to-end wiring of pre-existing `_drift_walk()` wait parameters, fuel-saved field addition, frontend badge.

- **B:** `optimize_route()` and `solve_forced_route()` each gained `max_wait_days=0.0` and `min_saving_km_s=0.0` params, forwarded to their respective `_drift_walk()` calls. Previously, `_drift_walk()` accepted both params but neither caller ever passed them — wait-window optimization was unreachable outside direct unit tests.
- **C:** `PlanRequest` and `MissionCostRequest` both gained `max_wait_days: float = Field(0.0, ge=0, le=30)` and `min_saving_km_s: float = Field(0.0, ge=0)`. Both `/plan` and `/mission-cost` thread these through to their respective optimizer calls.
- **B (fuel-saved gap):** `_drift_walk()` now adds `"fuel_saved_km_s"` to every step dict (`round(drifted_cost - best_cost, 4)` when a wait is recommended, else `0.0`). Both `optimize_route()` and `solve_forced_route()` output dicts gained `total_fuel_saved_km_s` (sum of per-step values). `naive_route` got `"fuel_saved_km_s": 0.0` for shape parity. `delta_v_km_s` and `total_fuel_cost_km_s` semantics unchanged — fuel-saved is additive and informational only.
- **Frontend:** `PlanForm.jsx` advanced options gained a "Max wait (days)" numeric input (same pattern as "Nets carried"). `CustomSelectionSummary.jsx` gained equivalent local state threaded through `handleCompute` → `onConfirm` → `buildMissionCostPayload()` in `App.jsx`. "Fuel saved by waiting" stat added to `ReasoningPanel.jsx`, `CustomSelectionSummary.jsx`, and the inline mission-cost stats block in `App.jsx` — rendered only when `total_fuel_saved_km_s > 0`.
- **Bugs fixed (1):** Wait-window feature was silently unreachable in production — `max_wait_days` and `min_saving_km_s` existed only in `_drift_walk()`'s signature, never exposed via any request model or optimizer caller.
- **Testing:** `_EXPECTED_STEP_KEYS` and `_EXPECTED_FORCED_STEP_KEYS` updated to include `"fuel_saved_km_s"`. Two new tests: `test_total_fuel_saved_zero_when_max_wait_zero` (regression guard) and `test_total_fuel_saved_positive_when_wait_recommended`. Suite: 75 → 76 passing. 2 pre-existing failures (LLM explanation cache, Groq fallback) unaffected.
- **Verification:** live curl with `max_wait_days=14` against `/mission-cost` confirmed `recommended_wait_days=14`, `fuel_saved_km_s=1.1006`, `total_fuel_saved_km_s=1.1006`. Byte-for-byte diff confirmed `max_wait_days=0.0` vs omitted produces identical output.

## Replan Diff Visualization + Globe UX rework

**Built with IBM Bob.**

- **Frontend (route tab strip):** `App.jsx` gained `routeTabs` / `activeRouteTabIdx` state. Each new `/plan` resets the strip to a pinned `Plan` tab; each replan appends `Replan #N` (capped at 5 replan tabs via `MAX_ROUTE_REPLAN_TABS` — oldest replan tab evicted at cap, Plan tab always pinned). Each tab carries a `type: 'plan' | 'replan'` field. Active tab auto-advances to newest. Clicking any tab swaps the globe route. Strip renders top-center of the globe pane, stacks below the "Clear All" button when both are visible simultaneously.
- **Frontend (route-line recency coloring):** route color is driven by tab `type` + recency rather than a per-leg diff: `white` when no replan exists yet, `#B4FF00` for the current latest replan, `orange` for every other tab (the original plan and any superseded replan). Single `routeColor` value applied to the whole polyline per tab.
- **Frontend (diff highlight):** `diffHighlightIds` derived via `useMemo` — Set of NORAD IDs present in the active replan tab but absent from the previous tab. Passed to `DebrisGlobe`; matching debris dots render cyan (`#00e5ff` α0.95) with size boost, identical to the custom-selection highlight style.
- **Frontend (highlight dim mode):** `focusMode === 'dim'` (HIGHLIGHT button) now visually dims all non-route debris dots to α0.18, leaving route stops at full brightness. Previously, the dim branch was a no-op.
- **Frontend (route line):** AI route renders as a solid white `Color.WHITE` polyline (`width=3`, `disableDepthTestDistance=POSITIVE_INFINITY`). Naive route renders as gray `PolylineDashMaterialProperty` (`#8A8A8E` α0.85, dashLength 16, `width=2`). Both have `disableDepthTestDistance` — fixes a bug where the naive polyline disappeared when it passed behind the globe sphere.
- **Frontend (debris reasoning scope fix):** `activeRouteNoradIds` was reconstructed as a `new Set(...)` inline on every App render. The `useEffect` dependency in `DebrisInfoModal` on this reference caused the Reason tab to be overwritten back to Info immediately after opening, even for objects in the current route. Fixed by lifting `activeRouteNoradIds` into a `useMemo` keyed on `activePlan?.route` / `activePlan?.route_details` — the Set reference is now stable across renders and only changes when the route content actually changes. Dependency array in the tab-reset effect updated to include `activeRouteNoradIds` so stale reasoning is cleared correctly after a replan removes a debris object from the route.
- **Frontend (manifest leg-focus):** `ReasoningPanel` gained optional `globeRef` and `debrisField` props (threaded from `App.jsx` → `EntryDetailView` → `ReasoningPanel`). When both are present, each manifest row's leg-index number becomes a `.leg-index-btn` button: click pans the globe camera to that stop's coordinates via `globeRef.current.flyTo(longitude, latitude, altitude_km)`. Hover lightens the number toward `var(--c-ink)` via CSS transition. Non-index cells remain non-interactive.
- **Bugs fixed (3):** (1) Naive route polyline depth-clipping (missing `disableDepthTestDistance`). (2) Reason tab immediately forced back to Info for in-route objects on any App re-render (stale Set reference). (3) Clear All button and route-tab-strip both `top:12px; left:50%` — stacking overlap resolved with `.route-tab-strip--below-clear-all` modifier class (`top:46px`) applied when `pinnedDebris.size >= 2`.
- **Files changed:** `App.jsx`, `DebrisGlobe.jsx`, `DebrisInfoModal.jsx`, `ReasoningPanel.jsx`, `global.css`, `dashboard.test.jsx` (mock updates only).
- **Testing:** Suite: 223 total, 220 passing. 3 pre-existing failures (2 LLM: `test_explanation_cache_limits_llm_calls`, `test_explanation_fallback_on_groq_failure`; 1 unrelated: `test_nets_carried_monotonic_with_cap`) — unaffected. Frontend (vitest): 15 total, 6 passing; 9 pre-existing failures in `dashboard.test.jsx` from the Phase 1 tab-panel layout migration (`section-workspace` now nested under `dashboard-pane`, tests still query it as top-level) — unaffected.