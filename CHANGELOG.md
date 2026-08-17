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

## Confidence-Aware Reasoning (data_quality → LLM narration + badges)

**Built with IBM Bob.**

- **B (optimizer):** `_drift_walk()` step dict gained `"data_quality": to_node.get("data_quality", "unknown")` — covers both `optimize_route()` and `solve_forced_route()` since both call `_drift_walk()`. `optimize_route()` and `solve_forced_route()` route_details dicts each gained `"data_quality": o.get("data_quality", "unknown")`.
- **C:** `naive_route()`'s own step dict and route_details dict gained the same `data_quality` key (naive_route doesn't call `_drift_walk()`, so wired separately). `_explain_plan()` gained a `data_quality_counts` dict (same aggregation pattern as the existing `method_counts`), included in the `json.dumps(...)` prompt payload; one instruction line added telling the model to hedge tone ("likely", "estimated") for aging/stale objects and stay confident when all objects are fresh. 2–3 sentence output constraint unchanged.
- **Frontend:** New shared `DataQualityBadge.jsx` component — brightness-only (no hue, per the grayscale design constraint), three variants (`fresh` / `aging` / `stale`) using `--c-ink` / `--c-steel` / `--c-line` tokens, mono font, uppercase, bordered box matching the existing `.reasoning .warning` precedent. Used in both `DebrisInfoModal.jsx` (Info tab's "Data quality" row) and `ReasoningPanel.jsx` (new "Data" column in the flight manifest table) — no duplicated CSS/markup.
- **Bugs fixed:** 0 (shipped clean — surfaces an already-computed field, no optimizer/risk-scoring logic touched).
- **Testing:** `_EXPECTED_STEP_KEYS` and `_EXPECTED_FORCED_STEP_KEYS` updated to include `"data_quality"`. Suite: 223 total, 221 passing. 2 failures (`test_explanation_cache_limits_llm_calls`, `test_explanation_fallback_on_groq_failure`) — both pre-existing/LLM-related. Note: `test_nets_carried_monotonic_with_cap`, previously listed as a pre-existing failure, now passes; cause not yet isolated to this change.
- **Verification:** live query against `/debris-field` found 5 real non-fresh objects (2 aging, 2 stale, by TLE epoch age 8–25 days). Route fed to `_explain_plan()` with one aging + two fresh objects produced narration correctly hedging only the aging object ("likely") while stating deterministic fuel/budget figures flatly — confirmed via actual LLM output, not self-report. Badge rendering confirmed live in both `DebrisInfoModal` (Info tab) and `ReasoningPanel` (manifest table) via screenshots.

## Decision Provenance Inspector

**Built with IBM Bob.**

- **C:** New `GET /leg/{from_norad_id}/{to_norad_id}/explanation` endpoint (`app/main.py:1775`). Leg metrics (`delta_v_km_s`, `fuel_saved_km_s`, `recommended_wait_days`, `raan_drift_deg`, `arrival_time_days`) are supplied as query parameters — the step_breakdown values already computed by the solver, passed through without a second solve. Both endpoint objects are looked up in the scored debris field; `from_norad_id = -1` is accepted as the depot sentinel. Single Groq call (`openai/gpt-oss-20b` — same model/cost class as the per-object removal-method reasoning endpoint, intentionally distinct from the `120b` route-briefing model). Response shape: `{from_norad_id, to_norad_id, from_obj, to_obj, delta_v_km_s, fuel_saved_km_s, recommended_wait_days, raan_drift_deg, arrival_time_days, explanation, explanation_unavailable}`. Never returns 500 — `explanation_unavailable: true` on LLM failure.
- **C (cache):** Server-side `_leg_explanation_cache: dict[tuple[int, int], dict]` (`app/main.py:1771`) keyed by `(from_norad_id, to_norad_id)` pair, cached for the process lifetime — same pattern as `_reasoning_cache`. Bounded by (visited nodes)² per session; in practice a handful of entries.
- **Frontend (new component):** `LegDetailPanel.jsx` — overlay panel (reuses `.debris-modal` positioning and all CSS tokens from `DebrisInfoModal`) showing FROM → TO endpoint cards side-by-side (name, NORAD ID, `DataQualityBadge`, TLE epoch age, risk score), a Transfer Cost section (delta-v, arrival day, RAAN drift), an optional J2 Nodal Drift Wait section (shown only when `recommended_wait_days > 0`), and the LLM explanation paragraph — all in a single tab, consistent with the spec. Client-side cache via `useRef(new Map())` keyed by `"fromId:toId"` — same pattern as `reasoningCacheRef` in `DebrisInfoModal.jsx`.
- **Frontend (`api.js:86`):** `getLegExplanation(fromNoradId, toNoradId, step)` — builds query-string from step_breakdown fields and calls the new endpoint.
- **Frontend (`ReasoningPanel.jsx:33`, lines 92–165):** Two new props: `onLegClick(step, fromNoradId, toNoradId, legIndex)` and `onDebrisSelect(debris)`. Leg-index button now calls `globeRef?.current?.flyToLeg(fromDebris, toDebris)` (bounding-view framing both endpoints) **and** fires `onLegClick` — the previous single-point `flyTo` on the TO object is replaced. FROM and TO name cells become `.manifest-debris-name-btn` underline-link buttons when `debrisField` and `onDebrisSelect` are present; clicking calls `globeRef?.current?.flyTo(longitude, latitude, altitude_km)` then `onDebrisSelect(debris)` — camera pans to that object and opens `DebrisInfoModal`, identical behavior to clicking the dot on the globe.
- **Frontend (`DebrisGlobe.jsx:3`, lines 155–198):** `BoundingSphere` added to Cesium imports. New `flyToLeg(fromDebris, toDebris)` method on the imperative handle — computes midpoint of both Cartesian3 positions as sphere center; radius = half separation × 1.2 margin, floored at 400 km. Calls `camera.flyToBoundingSphere()` with `duration: 1.5`. Depot legs (`fromDebris` null) fall back to a single-point bounding sphere on TO at 800 km radius.
- **Frontend (`App.jsx`):** `LegDetailPanel` import (`line 4`); `activeLeg` state (`line 355`); `handleLegClick` / `handleLegClose` (`lines 629–637`, closes debris modal on open to prevent overlap); `onLegClick` / `onDebrisSelect` threaded through `EntryDetailView` signature (`line 83`) and all three `<ReasoningPanel>` call-sites (`lines 141, 155, 186`); `<LegDetailPanel>` rendered in globe pane (`lines 1018–1025`); both callbacks passed at the `<EntryDetailView>` call-site in the Workspace panel (`lines 1219–1220`).
- **Frontend (`global.css`):** `.leg-panel-endpoint-*` layout tokens for FROM → TO side-by-side cards; `.manifest-debris-name-btn` underline-link style (underline color transitions from `--c-line` to `--c-steel` on hover; text transitions to `--c-signal`).
- **Bugs fixed:** 0 (shipped clean — all three camera behaviors, caches, and fallbacks worked on first manual test pass).
- **Testing:** Backend: 223 total, 220 passing. 3 pre-existing failures (`test_nets_carried_monotonic_with_cap` OR-Tools stochastic monotonicity; `test_explanation_cache_limits_llm_calls`; `test_explanation_fallback_on_groq_failure` Groq mock) — all confirmed pre-existing by stash-and-rerun before merge, unaffected by this change. Frontend (vitest): 15 total, 6 passing; 9 pre-existing failures in `dashboard.test.jsx` (same Phase 1 tab-panel layout failures as previous entry) — unaffected.
- **Verification:** `curl "http://localhost:8000/leg/24946/46734/explanation?delta_v_km_s=0.14&raan_drift_deg=2.1&arrival_time_days=1.4"` returned real LLM narration: *"The transfer requires only a modest velocity change of 0.14 km/s because the target debris is only slightly lower in altitude and the RAAN difference is modest… Since both source and destination objects have fresh data and similar risk scores, the trajectory uncertainty is low, so the cost remains predictable and relatively low."* `explanation_unavailable: false`. Live-UI manual test confirmed: (a) clicking a manifest leg number opens the leg panel with FROM/TO cards, delta-v math, and narration in one tab; (b) clicking a manifest debris name opens `DebrisInfoModal` and pans the camera to that object; (c) leg-click camera frames both FROM and TO debris inside the bounding sphere view.

## Trade-off Plan Comparator (POST /compare) + "Use these weights" preset flow

**Built with IBM Bob.**

- **C:** New `POST /compare` endpoint (`app/main.py`) runs 3 fixed weight presets — `Fuel-Conservative` (`proximity:0.70, lifetime:0.15, size:0.15`), `Balanced` (= `DEFAULT_WEIGHTS`), `Risk-Aggressive` (`proximity:0.15, lifetime:0.45, size:0.40`) — concurrently via `ThreadPoolExecutor(max_workers=3)`. OR-Tools' `Solve()` releases the GIL, so real parallelism: sequential 15.17s → concurrent 5.28s (2.87× speedup, confirmed by timing both paths). Response: `{presets: [{label, weights, total_fuel_cost_km_s, total_risk_collected, visited_count, route_details}], comparison_narration}`. Exactly one LLM call (`openai/gpt-oss-120b`, same model class as `_explain_plan`) narrates all 3 results together — not one call per preset. Narration cached via `_compare_narration_cache`, keyed by a SHA-256 hash of the 3 preset weight dicts + relevant request params (fuel budget, pool size, launch site, etc.); optimizer runs themselves are never cached.
- **Frontend (comparison view):** New `ComparisonPanel.jsx` — "Compare Presets" button next to "Generate Plan" in `PlanForm.jsx`. Renders 3 stat cards (fuel/risk/visited per preset) plus a grouped Recharts bar chart (fuel + risk per preset) and the narration paragraph. Grayscale-only per-preset brightness (Fuel-Conservative dim, Balanced mid, Risk-Aggressive bright) — no new hues, matches the existing `DataQualityBadge.jsx` color rule.
- **Frontend (preset → weights, not preset → fake plan):** Each stat card's button reads **"Use these weights"** (not "Use this plan"). Clicking it does *not* fabricate a result or commit a History entry — it populates `PlanForm`'s `weights_json` textarea with that preset's weights via a new `presetWeights` prop and closes the comparison panel. The user then clicks the real "Generate Plan" button to get an actual `/plan` result — real solve, real `step_breakdown`-driven manifest, real `_explain_plan` narration — through the exact same code path as any normal plan. An earlier version of `handleUsePlan` fabricated a fake result client-side (canned one-line "explanation," no manifest, no real narration); this was removed entirely rather than patched, since `/compare`'s `route_details` never carried the fields a real plan result needs.
- **Bugs fixed (1):** Same-preset re-click silently no-op'd. `preset.weights` is the same object reference across renders (mocked/cached response), so `setPresetWeightsToApply(preset.weights)` was a no-op under `Object.is` — React bailed, `PlanForm`'s `useEffect` never re-fired, `weights_json` stayed at whatever the user had manually edited it to. Fixed by wrapping state as `{weights, seq}` with a `useRef` counter incremented on every `handleUsePlan` call, guaranteeing a new reference regardless of weights-value identity.
- **Testing:** New `preset-weights.test.jsx` — 7 tests: preset click sets `weights_json` correctly; second preset click replaces (not merges) the first; same-preset re-click after a manual edit still resets correctly (regression test for the bug above); other form fields (fuel budget, pool size, nets carried) untouched by a preset click; `ComparisonPanel` unmounts on click; clicking a preset produces no History/plan/workspace state; Generate Plan after a preset click produces a real result via `api.plan`. Backend: 181 passing, 1 pre-existing failure (`test_explanation_cache_limits_llm_calls`, LLM/network-related, unaffected). Frontend (vitest): 22 total, 13 passing, 9 pre-existing failures in `dashboard.test.jsx` (same Phase 1 tab-panel layout migration failures as prior entries — confirmed unrelated by stash-and-rerun against pre-task state) — unaffected.
- **Verification:** Live curl against `/compare` (`fuel_budget=5.0, pool_size=20`) confirmed all 3 presets present with correct weights, full `route_details`, and narration correctly attributing fuel differences to pool composition rather than direct fuel optimization (weights feed `select_candidate_pool`'s risk ranking, not the solver's fuel objective — `optimizer.py` line 7). `git log` re-clone confirmed the commit landed on remote before being marked complete.

## Launch-Window Pareto Explorer (POST /sweep-launch-window)

**Built with IBM Bob.**

- **B (optimizer):** New pure function `compute_pareto_frontier(results, *, forced)` — `forced=True` (Custom Selection's `forced_target_ids` present) returns `sweep_mode="single_axis"`, marking only the single lowest-fuel date optimal (ties broken by lower `day_offset`); `forced=False` returns `sweep_mode="pareto_frontier"`, a real two-axis dominance check (a date is optimal if no other date has both lower-or-equal fuel cost and higher-or-equal risk collected, with at least one strict inequality). `sweep_mode` is set directly from the `forced` boolean — never inferred from comparing `total_risk_collected` values across results, to avoid floating-point-driven misclassification. Error-keyed results excluded from frontier computation, marked `is_pareto_optimal: False`. Input dicts never mutated (returns copies).
- **C:** New `_debris_epoch()` — single UTC anchor derived from the TLE cache file's `mtime` (extracted from the existing `get_cache_timestamp()` logic in `tle_fetch.py`), used by both the sweep and `/plan` in place of wall-clock `date.today()`. Called exactly once per `/sweep-launch-window` request, before the `ThreadPoolExecutor` block starts, and the frozen value is passed into every concurrent worker rather than re-derived per thread. `_get_scored_field()` called unconditionally before `_debris_epoch()` in both handlers — structural, not incidental, ordering (guards the cold-start `FileNotFoundError` case). New `SweepLaunchWindowRequest`, `_sweep_narration_cache`, `_sweep_cache_key()`, `_worst_data_quality()`, `_explain_sweep()`, `sweep_launch_window()`. Coarse sweep runs `raan_drift_deg()` + `_build_depot_node()` per date-offset, solved concurrently (`solve_forced_route()` when `forced_target_ids` present, `_run_plan()`'s standard path otherwise — drift pre-applied via `shifted_raan`, never re-applied inside `_run_plan`). Refine pass adds two solves at ±0.5 day around each local fuel-cost minimum found in the coarse sweep. One Groq call (`openai/gpt-oss-120b`, same class as `_explain_plan`) narrates the full result set — in `pareto_frontier` mode explicitly frames `lowest_fuel_date` as one reference point, not a universal recommendation. `PlanRequest.launch_date` (new optional field) threads a clicked date into `/plan`: absent → zero drift (unchanged default behavior), present → drift computed against `_debris_epoch()`, rejected with 422 beyond `_MAX_LAUNCH_DAY_OFFSET = 14.0` (shared constant, same 14-day TLE reliability window used elsewhere).
- **Frontend:** New `LaunchWindowPanel.jsx` — `pareto_frontier` mode renders a Recharts `ScatterChart` (fuel vs. risk, frontier points bright/dominated points dim, `lowest_fuel_date` marked with a dashed ring and an explicit "one reference point" label, never highlighted as "the answer"); `single_axis` mode renders a `BarChart` by date instead, no scatter, no implied trade-off. Exports `filterParetoOptimal` (pure filter on the backend's `is_pareto_optimal` flag — does not recompute dominance client-side) and `findLowestFuelEntry` (mirrors the backend's tie-break rule: lower `day_offset` wins). New `hasConstantRisk(window)` — when every valid entry in a `pareto_frontier` result shares the same `total_risk_collected` (the frontier mathematically collapses to the single minimum-fuel point because launch date changes delta-v but not which targets get selected), an inline note renders above the chart explaining why the frontier looks like a flat line with one highlighted point rather than a spread. Clicking a chart point calls `onSelectDate(launch_date)` — sets `PlanForm`'s `launch_date` field only via a plain `useState` setter (unconditional replace, no merge/append), no auto-submit, no History entry, mirroring the "Use these weights" pattern from the Trade-off Plan Comparator exactly. `PlanForm.jsx` gained `onSweep`/`sweeping`/`sweepLaunchDate` props, an "Explore Launch Windows" button next to both the launch-site pin and the custom-orbit "Pin orbit" button, and a new `launch_date` text field (populated by sweep click, also manually editable) using the same seq-nonce effect pattern as `presetWeights`. `App.jsx` gained `handleSweep`/`handleSelectSweepDate` and sweep state; `api.js` gained `sweepLaunchWindow()`.
- **Bugs fixed (5), all caught in review before or shortly after merge, none discovered by Bob unprompted:** (1) Initial schema proposal inferred `sweep_mode` from `len(set(total_risk_collected)) == 1`, fragile to floating-point drift across independently-run solves — fixed to read directly from `forced_target_ids`. (2) `day_offset` initially typed as integer, incompatible with the ±12hr refine-pass requirement — fixed to float. (3) First proposed date-anchor helper (`_sweep_today()`) still used wall-clock `datetime.now(utc)`, reintroducing the same midnight-UTC-boundary drift it was meant to fix (a plan generated after clicking a chart point could silently resolve to a different `day_offset` than the point shown) — fixed by anchoring to the TLE cache `mtime` instead of wall-clock time. (4) Epoch not initially frozen across concurrent sweep workers — a mid-sweep TLE cache refresh could have anchored different chart points to different epochs with no visible symptom — fixed to call `_debris_epoch()` once and pass the frozen value into every worker. (5) `total_risk_collected` flat across every date in a naturally-occurring (non-`forced`) case produced a frontier chart that was mathematically correct but visually read as broken (single highlighted point, flat line, labeled "Pareto Frontier") — fixed with the `hasConstantRisk` inline note; display-only, does not touch `compute_pareto_frontier` or `sweep_mode` logic.
- **Testing:** Backend: 276 passed, 0 new failures (same 2 pre-existing unrelated failures — `test_nets_carried_monotonic_with_cap`, `test_explanation_fallback_on_groq_failure` — confirmed unchanged on baseline). New `tests/test_sweep_launch_window.py`, 35 tests: `TestComputeParetoFrontierSingleAxis` (7), `TestComputeParetoFrontierParetoMode` (7), `TestSweepEndpointShape` (7), `TestSweepSingleAxisBranch` (2, including explicit confirmation `solve_forced_route` fires and `_run_plan` does not in `single_axis` mode), `TestSweepNarrationCaching` (4), `TestLaunchDateGuardrails` (8, all three `/plan` guardrails plus boundary conditions), `TestFrozenEpochUnderConcurrency` (2, added after direct review — patches `os.path.getmtime` to return a different value per call and asserts every result in one sweep window is anchored to the same epoch; asserts `_debris_epoch()` is called exactly once regardless of worker count), `TestNoDoubleDrift` (2, added after direct review — asserts no internal `PlanRequest` built inside the sweep ever carries `launch_date`; asserts the actual RAAN delivered to `optimize_route()` matches single-drift, not double-drift, within 0.001°). Frontend: 22 new tests across `launch-window-panel.test.jsx` — 12 initial (`filterParetoOptimal` basic correctness ×4, the two Q3 flag-trust-vs-recomputation tests, immutability ×1, `findLowestFuelEntry` basic behavior ×3, null/missing-data handling ×2), plus 3 added after review for the launch-date replace-on-click contract (second click fully replaces the first; same-date re-click still sets correctly; click after a manual text edit overwrites the manual value), plus 7 for `hasConstantRisk` (all-same-value, differing values, error entries ignored, null risk ignored, empty window, single entry, no input mutation). Confirmed via grep across all five pre-existing frontend test files that none of `filterParetoOptimal`, `findLowestFuelEntry`, `is_pareto_optimal`, `lowest_fuel`, or `hasConstantRisk` had any prior coverage — all 22 are new ground, none duplicate existing tests.
- **Verification:** Live UI screenshot confirmed a real 14-day sweep (`pareto_frontier` mode): fuel cost ranged ~44.8 km/s (earliest launch) down to ~20.2 km/s (day 14), risk collected constant at 3.76 across every date, day 14 correctly the sole Pareto-optimal point (identical to the minimum-fuel point in this case, by construction, not by a mode-logic bug), `hasConstantRisk` note rendering correctly above the chart. Clicking a manifest date populated `PlanForm`'s `launch_date` field and correctly overwrote a prior value on a second click, confirmed against the new replace-on-click tests rather than assumed from the seq-nonce pattern transferring automatically from the Comparator.

## Anomaly Replan (scoped-down: exclude_norad_ids + start-position override)

**Built with IBM Bob.**

- **C:** Two additions to the existing `POST /replan` `applied_proposal` path only — no new endpoint, no free-text LLM parsing touched (`_ALLOWED_OVERRIDE_KEYS` and `_build_parse_prompt()` untouched, confirmed by grep). (1) `exclude_norad_ids: list[int]` field on `ReplanRequest` — filtered out of the candidate pool in `_run_plan()` before pool selection, applies to `new_plan` only. (2) `start_altitude_km`/`start_inclination_deg` override branch in `_execute_overrides()`'s Step-3 validation, following the existing `fuel_budget_km_s` block's shape — lets `/replan` reroute from an arbitrary current orbit instead of a named `launch_site`; `start_raan_deg` optional, falls back to the field default.
- **Design decision:** spec originally required `start_altitude_km`/`start_inclination_deg` to always arrive together. Discovered the pre-existing `altitude_expand` fix-suggestion type sends `start_altitude_km` alone (no inclination) through the same path — strict enforcement would have broken it (confirmed via `test_altitude_expand_real_shape_translates`). Resolved asymmetrically: `start_inclination_deg` always requires `start_altitude_km` alongside it; `start_altitude_km` alone is valid (keeps existing inclination, matches `altitude_expand`'s existing behavior).
- **Bugs fixed (2), both caught in review after initial merge, neither by Bob unprompted:** (1) `old_plan` was built from the original `req` object, which still carried `exclude_norad_ids` — silently filtering `old_plan` too, contradicting the field's own "new plan only" docstring and defeating the diff's ability to show what was dropped because of the anomaly. Fixed via `req.model_copy(update={"exclude_norad_ids": []})` before the `old_plan` call. (2) `new_plan` was built from a `PlanRequest` with `exclude_norad_ids` stripped entirely (`PlanRequest` has no such field) — the filter never actually fired on `new_plan` either, so the feature had zero real effect end-to-end despite passing tests, because every prior test called `_run_plan()` directly with a `ReplanRequest` rather than through the real `_execute_overrides()` path. Fixed by adding an explicit `exclude_norad_ids` keyword-only parameter to `_run_plan()` (falls back to `getattr(req, ...)` for direct callers), with `_execute_overrides()` passing `req.exclude_norad_ids` explicitly into the `new_plan` call.
- **Testing:** New `tests/test_anomaly_replan.py`, 19 tests total across three passes: field/filter unit tests, `start_altitude_km`/`start_inclination_deg` validation + `_ALLOWED_OVERRIDE_KEYS` exclusion checks, and a dedicated `TestOldPlanExcludeNoradIdsNotLeaked` class added for the two bug fixes (real, unmocked `_execute_overrides()` calls — confirms the excluded ID is present in `old_plan`, absent from `new_plan`, and that `req` itself is never mutated). All 19 passing. Broader suite (every file touched or calling `_run_plan()` directly): 218 passed, 0 new failures. `test_new_features.py`: 77 passed, 2 pre-existing failures (`test_nets_carried_monotonic_with_cap`, `test_explanation_fallback_on_groq_failure`) — both confirmed pre-existing via stash-and-rerun against baseline, unrelated to this change.
- **Verification:** Static code review against the actual pushed repo (not self-report) across all three commits (`929527c` initial feature, `97a54a7` old_plan fix, `aeb69a2` new_plan fix) — confirmed `_ALLOWED_OVERRIDE_KEYS` never gained the new override keys, and confirmed the `old_plan`/`new_plan` call sites match the reported diffs exactly.

## Reachability-aware candidate pool (select_candidate_pool depot/fuel-budget filter)

**Built with IBM Bob.**

- **B (cost_matrix.py):** `select_candidate_pool()` gained optional `depot`/`fuel_budget_km_s` params. `depot=None` (default) is byte-for-byte the old pure risk-sort behavior — every existing caller that omits these params (diagnostic scripts, `__main__` self-tests, existing backward-compat test) is unaffected. When both are supplied: a two-stage TRUE shortest-path reachability filter runs before the risk-sort/slice, replacing the earlier design (a simple direct depot→object delta-v cutoff) that was scoped but never shipped after review found it could wrongly exclude objects reachable more cheaply via an intermediate hop. Stage 1 is a cheap direct-hop prefilter at `STAGE1_SLACK_MULTIPLIER = 3.0` × `fuel_budget_km_s`, bounding the candidate set before the expensive step. Stage 2 builds the full pairwise delta-v matrix over that bounded superset (`build_cost_matrix()`, unchanged) and runs a new `compute_reachable_costs()` — Dijkstra from the depot node — to get the true cheapest-path cost to every object, direct or via intermediates. Only objects with shortest-path cost ≤ `fuel_budget_km_s` survive to the risk-sort/slice step. Delta-v costs are non-negative by construction, so Dijkstra is the correct algorithm here.
- **B (optimizer.py, main.py):** `min_depot_hop_km_s` empty-pool fallback fixed in both `optimizer.py` and the `/naive-route` handler in `main.py` — was silently `0.0` when the pool was empty, reading as "any hop is free"; now `None`, with the dependent warning text branching correctly on it instead of stating a misleading cost.
- **C:** Both real production call sites wired: `_run_plan()` (covers `/plan`, `/compare`, and `/sweep-launch-window`'s `pareto_frontier` mode for free, since all three route through it) now passes `depot`/`fuel_budget_km_s` using values already in scope (`start_altitude_km`, `start_inclination_deg`, `effective_raan_deg`, `req.fuel_budget_km_s`). `/naive-route` required its own patch — its `depot` dict construction was moved earlier in the handler so it's available before the `select_candidate_pool()` call, keeping the naive-vs-AI comparison fair (same pool composition for identical inputs). `solve_forced_route()` and the `single_axis` sweep mode don't call `select_candidate_pool()` at all and were correctly left untouched. No new request fields, no frontend changes — depot/budget values were already flowing through existing requests.
- **Design correctness check:** the Stage-1 slack constant's safety was independently verified by hand against the project's real data range, not just asserted in a docstring — see the noted test-rigor gap below.
- **Bugs fixed (0 in the shipped code path)** — but one design gap caught in review before merge: the originally-drafted Stage-1 test (`test_stage1_includes_all_stage2_survivors`) sets `fuel_budget_km_s` to 1.1× the maximum shortest-path cost across its whole synthetic field, which makes every object in that field trivially pass Stage 1 regardless of whether the invariant genuinely holds near the real cutoff boundary — the test doesn't fail, but it also doesn't prove what it claims to. Not fixed in this pass (flagged, not blocking); see Verification below for the independent check that was done instead.
- **Testing:** New `tests/test_reachability_pool.py` — `TestComputeReachableCosts` (Dijkstra correctness on synthetic graphs, including a case where the shortest path is not the direct hop), `TestStage1SupersetInvariant` (the invariant test noted above), `TestSelectCandidatePoolWithDepot` (risk-sort-after-filter behavior), `TestEmptyReachableSet`, `TestNaiveVsPlanPoolParity`, `TestPerformance`. 17/19 passing standalone; the 2 failures both require live Celestrak data and fail identically on the pre-fix baseline commit for the same sandbox network-allowlist reason documented for Feature 4's endpoint tests — not a code defect. `test_select_candidate_pool_backward_compat_no_removal_method_key` (existing) passes unmodified. `pool_size_increase` dry-run test: 3/3 passing. Full suite diffed directly against the pre-fix baseline commit: 59 failed/212 passed/26 errors (baseline) vs. 61 failed/229 passed/26 errors (this change) — every failure category on both sides traces to the same pre-existing Celestrak/Groq sandbox limitation, zero new regressions. Performance: 0.007s on a 300-object synthetic field (raw timing captured), against a 5s guardrail.
- **Verification:** Independently stress-tested the Stage-1-superset safety claim by hand rather than trusting the loose test above — constructed adversarial synthetic scenarios (large plane-change transfers via high-altitude "bi-elliptic" intermediates) where a multi-hop path genuinely can beat a direct hop mathematically, confirming the effect is real. Restricted to this project's actual debris altitude range (~700–1000 km, matching Celestrak's real data for this project), the ratio of direct-hop cost to true shortest-path cost never exceeded 1.0 across the scenarios tested — i.e. multi-hop never beats direct for this app's real data, so `STAGE1_SLACK_MULTIPLIER = 3.0` is safe in practice today. Flagged for revisit if that constant changes or if a future feature widens the debris altitude range beyond LEO. Fresh clone + `git log` confirmed the commit (`4903722`) landed on `main` before being marked complete.