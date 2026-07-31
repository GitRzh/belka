## Module legend

| Module | Files | Purpose |
|---|---|---|
| A | `tle_fetch.py`, `risk_score.py` | Data + risk scoring |
| B | `delta_v.py`, `cost_matrix.py`, `optimizer.py` | Physics + optimizer |
| C | `main.py` | FastAPI layer |
| D | `removal_method.py` | Removal-method classification |
| E | `_explain_plan()` in `main.py` | LLM mission-briefing narration |

## Modules A, B, C (initial build)

Modules done: 3 (A, B, C)
- Module A — pulls live debris positions from Celestrak, filters to the
  700-1000km band, scores each object by risk (proximity + orbital
  lifetime).
- Module B — physics: Hohmann + inclination-change combined maneuver cost
  between any two orbits. Then an N×N cost matrix, then the actual
  OR-Tools orienteering solver — given a fuel budget, picks which debris
  to visit and in what order to maximize risk removed.
- Module C — wraps A+B in a FastAPI app: GET /debris-field,
  GET /debris/{norad_id}, POST /plan, GET /naive-route (baseline for the
  naive-vs-AI comparison in the demo).

Bugs found & fixed: 2
- Module A capped the merged debris list to 300 total after combining
  all three source clouds in fetch order, so Iridium-33 and Fengyun-1C got
  silently excluded entirely (one run came back 100% Cosmos). Fixed by
  capping each group independently before merging.
- Module B — skipped_objects was computed by matching object names, but
  real debris fragments share generic names (many different objects all
  literally named "COSMOS 2251 DEB"). So skipped_count/skipped_names were
  silently wrong whenever a name collision happened between a visited and a
  skipped object. Fixed to diff by pool index instead of name, and added
  (norad_id) to display labels so route output doesn't look like the same
  object visited 15 times. Verified fixed on live server — sum check: True.

## POST /replan (new endpoint)

Modules touched: 1 (C)

Built via IBM Bob — Wraps /plan with Groq-powered natural-language constraint parsing.
openai/gpt-oss-20b extracts parameter overrides from free text (fuel budget,
risk penalty, proximity/lifetime weights); openai/gpt-oss-120b explains the
before/after diff in plain language. Stateless — no plan ID, recomputes both
old and new plans fresh from the original request on every call.

Tested end-to-end against live Celestrak data:
- Relative constraint ("cut the fuel budget in half") — resolved correctly
  via injected current-value context in the extraction prompt.
- Single-weight override ("set proximity weight to 0.9") — complement logic
  correctly derives lifetime = 0.1 rather than renormalizing against the
  unchanged value.
- risk_penalty_scale override ("prioritize riskiest debris") — applied
  correctly; zero route diff confirmed as expected when pool is already
  fully visited.
- Off-topic input ("make it look cooler I guess") — clean no-op,
  overrides_applied: {}, old_plan == new_plan, re-verified after prompt
  rewrite to confirm richer context didn't make the model over-eager.
- Invalid override (weight = 5) — clean 422, rejected before reaching the
  optimizer.
- Groq timeout/connection failure — clean 503, no hang, no stack trace.
- Malformed JSON from LLM — retry fires and succeeds on valid second
  response; both-calls-invalid raises a specific ValueError. Covered by
  4 unit tests in app/test_parse_overrides.py (mocked, zero network calls).

Bugs found & fixed:
- _parse_overrides had no allowlist on the LLM's JSON output — a
  hallucinated key would pass straight into the overrides dict with
  nothing stopping future code from acting on it. Fixed with an explicit
  allowlist filter right after json.loads.
- Single-weight overrides were being renormalized against the *other*,
  unchanged weight, silently altering the user's explicit value (0.9 became
  ~0.69). Fixed: one weight specified -> derive the other as its
  complement; both specified and don't sum to 1 -> normalize both.
- Extraction prompt had no current parameter values or relative-instruction
  examples, so relative constraints ("cut the budget in half") silently
  fell back to no_changes with no baseline to compute from. Fixed by
  injecting live request values, natural-language aliases, and worked
  examples into the system prompt.
- Sub-millisecond timeout test surfaced an uncaught APIConnectionError
  (parent class) alongside the narrower APITimeoutError catch, causing a
  bare 500 instead of a clean error. Fixed by catching APIConnectionError,
  which covers the full "network didn't work" surface (timeouts,
  connection resets, refused connections, TLS-handshake aborts).

Also: main.py wasn't loading .env at all (no load_dotenv() call) — added
near the top, before any os.environ/os.getenv usage. Replaced a leftover
debug print with logger.debug() under the app.main logger. Added
pytest==9.1.1 to requirements.txt for the new unit test suite.

## Bug investigation — silent empty-route results in /plan and /replan

Modules touched: 2 (B, C)

A test run surfaced /plan and /replan silently returning visited_count: 0,
route: [] with a 200 OK and no error — with the exact same payload that
had previously returned a full route. Root-caused to three mechanisms:

1. risk_penalty_scale below a pool-dependent threshold (~2-3 on the
   current dataset) makes it cheaper for OR-Tools to skip every node than
   pay even the cheapest hop. The solver returns a valid, non-None
   solution, so main.py's `"error" in result` guard never fires.
2. fuel_budget_km_s below ~0.0005 km/s rounds to 0 in optimizer.py's
   `budget_scaled = round(fuel_budget_km_s * 1000)`, giving OR-Tools zero
   capacity — same silent empty-route result.
3. Celestrak cache refreshes shift per-hop delta-v costs and BSTAR-based
   risk rankings between runs, gradually changing which objects land in
   the top-N pool. Not the direct cause of the empty-route bug, but a
   contributing factor to run-to-run variance.

How it happened: the /replan feature introduced an LLM-driven
risk_penalty_scale override with only a negative-value check — a Groq
response like {"risk_penalty_scale": 1.5} passed validation, and a
subsequent /plan call with those params went silent.

Fixes applied:
- Added a minimum-value floor to risk_penalty_scale validation in /replan
  (Module C), rejecting values below the safe threshold with a 422 and a
  message explaining why. Note: the threshold is pool-dependent, so this
  floor is a best-effort filter, not a guarantee — see warning field below
  for the real safety net.
- Added the same floor pattern for fuel_budget_km_s, rejecting values that
  would round to zero fuel capacity in the optimizer (Module B).
- _run_plan (shared by both /plan and /replan) now injects a `warning`
  field into the response whenever visited_count == 0, explaining the
  likely cause and suggested fix, instead of returning a silent 200 OK
  with an empty route and no explanation. Chosen over a 4xx because
  visited_count: 0 can be a valid solver result for well-formed input —
  raising an error would be misleading and would break /replan's diff
  logic, which runs _run_plan twice and compares results.
- Fixed a narration bug in _explain_diff: budget_used_delta is a fractional
  value (0-1 scale), but the explanation prompt was phrasing it as a raw
  percentage, understating real budget-usage changes by ~100x.

Verified: full /plan -> /replan sequence against live Celestrak data
produces a real non-empty route, a correct diff, and no residual `warning`
key on the healthy path; degenerate inputs correctly surface either a 422
(validator floor) or a `warning` field (solver-level zero-visit case).

Note: the initial floor of 5 was later found insufficient — a
   cross-inclination start (~25° from the debris cluster) still degenerated
   at rps=5 (only 1 visit). Raised to 50 after confirming this clears
   reliably across tested scenarios.

## Removal-method recommendation (object_type + removal_method classification)

Modules touched: 3 (new: D; edited: B, C)

- New Module D — pure lookup-table classification, no LLM. Two real
  signals already present in Celestrak data: "DEB" in name marks a
  tracked fragment (absent = intact/parent object); bstar vs the batch
  median among fragments (area-to-mass proxy) splits fragments into
  larger (net_capture) vs smaller (monitor_only). Intact objects ->
  robotic_arm_or_net_capture. add_removal_methods() adds object_type +
  removal_method additively, same non-mutating pattern as
  score_debris_field().
- Module C — _get_scored_field() now calls add_removal_methods() once, on
  the full scored field, before pool selection. Deliberate: classifying
  per-pool instead would make the bstar threshold pool-size/weight-
  dependent, so the same norad_id could get a different removal_method
  in /debris/{id} vs inside a /plan route. Classifying once upstream
  means /debris-field, /debris/{norad_id}, /plan, and /replan all agree
  on the same object's classification.
- Module B — optimize_route() now returns route_details: full
  per-visited-object detail (norad_id, name, object_type, removal_method,
  risk_score) in solved visit order, alongside the existing route (string
  labels, left unchanged so _norad_ids_from_plan's regex parsing keeps
  working untouched).

Verified:
- Module D's own __main__ sanity block: intact objects ->
  robotic_arm_or_net_capture, fragments correctly split by batch-median
  bstar. Passing.
- Confirmed Module B's select_candidate_pool() is a pure sort-and-slice
  (no dict reconstruction) by reading the source directly — this had
  been flagged as an open assumption before confirming it, since a
  reconstructing implementation would have silently dropped the new
  fields at the pool-selection step.
- End-to-end sanity test (synthetic pool, mixed intact/fragment names,
  real select_candidate_pool + optimize_route): route_details populated
  with real classifications for 20/20 visited objects, zero
  "unknown"/"unclassified" fallbacks triggered.

Bugs found & fixed: 0 (clean wiring; no regressions to /replan's diff
logic or _norad_ids_from_plan's route-string parsing).

Not yet done (deferred, no code written):
- _explain_plan() — the LLM narration layer for /plan (hybrid design:
  lookup everywhere + narration only inside /plan's explanation) is still
  unbuilt.

## _explain_plan() — Module E, mission-briefing narration

Modules touched: 1 (new: E, in main.py)

- New _explain_plan() — Groq openai/gpt-oss-120b (same model as
  _explain_diff), given ONLY aggregated numbers (visited_count,
  removal_method_counts, fuel/risk totals, skipped_count), never raw
  per-object data — narrates, never decides. 2-3 sentence plain-English
  mission briefing.
- Retry logic: RateLimitError -> no retry (retrying into an active rate
  limit makes it worse), immediate soft-fail to None. APIConnectionError
  -> one retry with a 1.5s backoff, then soft-fail to None. Empty
  route_details -> short-circuits before any Groq call (nothing to
  narrate).
- Soft-fail contract: /plan and /replan never hard-fail on narration
  failure — explanation: null + explanation_error with a retry hint,
  route/plan data always returned regardless.
- Wired into POST /plan (after _run_plan() returns) and POST /replan:
  no_changes path narrates once (old_plan and new_plan are the same
  dict object, confirmed via live test); real-override path narrates
  ONLY new_plan (old_plan is being discarded, no value briefing a plan
  about to be replaced) — confirmed via live test that old_plan has no
  explanation key at all, new_plan does. Deliberate choice to avoid
  doubling Groq calls for zero benefit.

Verified: 9-step live test sequence, all passed, zero regressions —
epoch_age_days present/numeric/non-negative on all fetched objects; /plan
happy path returns real narration mentioning removal-method mix and a
generic skip reason; /plan tight-budget edge case returns explanation:
null with no explanation_error (visited_count==0 guard); /replan
no_changes path confirmed old_plan.explanation == new_plan.explanation
(same string); /replan real-override path confirmed old_plan has no
explanation key, new_plan does; mocked retry logic confirmed exactly as
designed (RateLimitError = 1 call no retry, APIConnectionError = 2 calls
with backoff, empty route_details = 0 calls).

Bugs found & fixed: 0 (clean addition).

## Method maturity, nets_carried cap, removal_method_filter, target_norad_id, naive-route parity

Modules touched: 3 (D, B, C)

- Module D — intact objects now unpack their bundled
  robotic_arm_or_net_capture label into possible_methods (both
  techniques) + method_maturity (per-technique real-world flight
  status: robotic_arm=conceptual, net_capture=flight_demonstrated).
  removal_method stays a bare string for backward compat.
- Module B — added a nets_carried cap as a real OR-Tools dimension
  (0 to nets_carried, fixed start at 0), not just advisory. route_details
  now also carries possible_methods/method_maturity per visited object.
  select_candidate_pool() now excludes monitor_only before sort/slice,
  so it never occupies a pool slot or gets routed.
- Module C — new removal_method_filter (robotic_arm_or_net_capture or
  net_capture only; monitor_only rejected, garbage values rejected, all
  via 422) on /plan and /replan. New target_norad_id forces a specific
  object into the pool even if outside the risk-score cutoff; rejected if
  the target is monitor_only or filtered out (404 with a hint pointing at
  removal_method_filter). /naive-route now returns route_details and an
  explanation, matching /plan's shape (soft-fails cleanly if the
  explainer errors, no 500).
- Housekeeping: replaced a second leftover debug print() (in replan())
  with logger.debug().

Verified: 30/30 unit tests passing (app/test_new_features.py), source
read directly to confirm test assertions match real behavior, not just
mocked shape.

Not yet done: frontend (Week 5) not started.

## README — Explainability + Real-World Grounding sections

Modules touched: 0 (docs only)

- Explainability section: spells out that removal_method is a
  deterministic lookup (removal_method.py, no LLM) and risk_score is an
  explicit weighted formula (risk_score.py) — LLM only narrates plans/
  diffs, never decides them. Flags risk_score as a relative within-batch
  ranking, not an absolute collision probability.
- Real-World Grounding section: cites NASA-TS-8719.14 and the Orbital
  Debris Program Office's ~5-highest-risk-objects/year finding as the
  premise behind risk-ranked optimization over naive nearest-neighbor.
  Grounds removal_method's two families against RemoveDEBRIS (net/harpoon,
  flown 2018-2019) and ELSA-d (magnetic capture, flown/de-orbited Jan
  2024); ClearSpace-1 noted as in-development, not flown. Explicitly
  discloses no verified per-mission delta-v/fuel figures were found
  (limitation, not an estimate). delta_v.py validated against 4 textbook
  orbital-mechanics benchmarks (LEO->GEO Hohmann, 90° plane change,
  GTO->GEO at Cape Canaveral and Kourou), reproducing the known
  equatorial-vs-non-equatorial GEO cost gap. Discloses nets_carried
  default of 1 as matching RemoveDEBRIS's actual flight history (it
  carried exactly one net); robotic_arm_or_net_capture's reusability
  assumption flagged as having no flown precedent on uncooperative
  debris (only real reusable-capture design, ELSA-M, needs a
  pre-installed docking plate); no collision/conjunction (CDM) screening
  performed, disclosed as a real operational step this tool doesn't
  model.

## Cache freshness display (GET /debris-field)

Modules touched: 2 (A, C) + frontend

- Module A — added get_cache_timestamp(), returning the debris cache
  file's mtime as an ISO 8601 UTC timestamp. Companion to the existing
  get_debris_field(), no change to that function's signature or return
  type.
- Module C — /debris-field's response shape changed from a bare list to
  a wrapped object: {debris_field, data_fetched_at, data_stale}.
  data_stale is true when cache age is within 10 minutes of
  CACHE_MAX_AGE_SECONDS (2hr), i.e. the next request will force a fresh
  Celestrak fetch. /debris/{norad_id}, /plan, and /replan untouched —
  all three call _get_scored_field() directly, which still returns a
  bare list internally.
- Frontend — App.jsx unpacks the new response shape and passes
  cacheMetadata down to DebrisGlobe.jsx, which displays a live-updating
  "Debris data: N min old" label (recalculated client-side every 30s
  from data_fetched_at, not a static string baked in at fetch time) plus
  an amber "refreshing soon" badge when data_stale is true.

Verified: confirmed via grep that App.jsx is the sole frontend consumer
of /debris-field (api.js's getDebrisField() is just the request
wrapper) — no other component was silently left expecting the old
bare-array shape.

Bugs found & fixed: 0 (clean addition).

## Phasing/timing — RAAN drift (bounded scope)

Modules touched: 2 (B) + main.py (no changes required)

Built via IBM Bob — recalled as fully Bob-built (user recollection from
this session's checkpoint discussion, not independently re-verified
against a diff; noted here as-is rather than left untracked). Worth
noting: this is the feature where the design-decision-first step was
skipped (the model choice — wait-time vs. reroute vs. cost-correction —
landed in code before it was discussed in chat), which is why the
scope-wording finding below exists.

Addresses one half of the "no phasing/timing" limitation flagged in
prior checkpoints: predicting where a target's orbital plane will
actually be by the time the spacecraft arrives, using elapsed mission
time. Does NOT address the other half — reordering the route to
minimize wait time — which stays explicitly out of scope (see docstring
note below).

- delta_v.py — added raan_drift_deg(altitude_km, inclination_deg,
  elapsed_days), a closed-form J2 secular RAAN drift approximation
  (dΩ/dt ≈ -1.5 * n * J2 * (Re/p)^2 * cos(i)), not live skyfield
  propagation (kept formula-based for per-request speed). Module
  docstring's "STILL NOT MODELED: phasing/timing" note split in two:
  RAAN drift is now modeled; true-anomaly phasing (in-plane timing,
  i.e. wait-time route optimization) is still not modeled.
- optimizer.py — the route-walking step now projects each candidate's
  RAAN forward by estimated cumulative elapsed time (heuristic:
  TRANSFER_TIME_DAYS_PER_KM_S = 10.0 mission days per km/s delta-v,
  untuned placeholder) before recomputing transfer cost with
  transfer_delta_v(), instead of using the target's static fetch-time
  RAAN for the whole route. Legs that become unreachable once drift is
  priced in are dropped (same treatment as fuel-budget exhaustion)
  rather than silently costed against stale data. New fields:
  arrival_time_days (route_details, step_breakdown), raan_drift_deg
  (step_breakdown).
- main.py — no changes needed; route_details/step_breakdown flow
  through _run_plan() to the response unfiltered, confirmed by reading
  the code directly rather than assuming.

Verified:
- Formula check: raan_drift_deg(800, 98, 1) = 0.917019 deg/day vs the
  known sun-synchronous rate of 0.9856 deg/day — correct sign
  (retrograde-orbit positive) and right order of magnitude; the ~0.07
  deg/day gap is expected since 800km/98.0deg is a rounded
  approximation of an exact SSO pair, not itself SSO-tuned.
- Live A/B at start_altitude_km=800, start_inclination_deg=98,
  start_raan_deg=222, fuel_budget_km_s=8.0: static-RAAN run visited 2
  objects (5.6526 km/s, risk 1.9848); drift-aware run visited 1
  (5.4921 km/s, risk 0.9882) — hop 2 correctly dropped once ~54.9
  elapsed days of drift (+50.4deg RAAN) pushed its true cost to ~6.7
  km/s, over the ~2.51 km/s remaining. Confirms the drift calculation
  is live and changes route selection, not just present in code unused.
- fuel_budget_km_s=2.0 (the original test value) returns 0 visits on
  both static and drift-aware runs — correct, not a bug: even the
  cheapest reachable hop from this cross-inclination start (3.099 km/s)
  exceeds the budget before drift is even a factor.

Not yet done: TRANSFER_TIME_DAYS_PER_KM_S is an untuned heuristic
constant, not derived from real mission pacing data — worth a one-line
README disclosure. Wait-time route reordering (resequencing stops to
minimize idle time, not just checking reachability) remains unbuilt and
out of scope for this pass.

Bugs found & fixed: 0 (clean addition).
## Straight-line route polyline fix (frontend)

Modules touched: 1 (frontend, `DebrisGlobe.jsx`)

Built via IBM Bob — scoped prompt after design decisions (arc method,
depot-leg treatment) were confirmed in chat; Bob's returned code was
independently verified against the actual pushed commit (`0ca1846`), not
just the summary Bob gave — see Verified note below.

Route legs were previously drawn as a straight chord between two stop
positions. Replaced with a new `slerpArc()` helper: spherical (slerp)
interpolation for direction, radius linearly interpolated between the
true start/end altitudes (not the mean of the two).

Two real bugs caught and fixed in the same pass, not shipped separately:
- Antipodal guard gap — the degenerate case at `omega ≈ 0` was already
  guarded (falls back to a straight lerp), but `omega ≈ π` (diametrically
  opposite stops) was not: `sin(omega) → 0` at both ends of the angular
  range, which would have produced garbage direction vectors for that
  case. Both ends now fall back correctly.
- Junction-altitude bug — every arc point, including the two endpoints,
  was being scaled to the *mean* altitude of the two stops rather than
  each stop's true altitude, so junctions visually floated up to ~22 km
  off the actual debris marker at realistic altitude spreads (verified
  against 3 live legs from real Celestrak data, 700-1000km band). Fixed
  by interpolating radius linearly from `rA` to `rB`, so `t=0` lands
  exactly on `a` and `t=1` exactly on `b`.

Also confirmed (no fix needed): the depot leg (route's first leg)
already gets identical arc treatment — depot is prepended into the
position array before arc expansion, so there's no special-casing gap.

Verified: diffed directly against the single commit that introduced
this (there was no prior shipped arc implementation to regress from —
this was a from-scratch build, not a fix to previously-committed code).
`vite build` clean.

Bugs found & fixed: 2 (antipodal guard, junction altitude).

Note: this is a smooth spherical interpolation with correct endpoint
altitudes, not a physically simulated transfer trajectory — disclosed
in README.

## SATCAT radar-cross-section join → risk_score size factor

Modules touched: 2 (A: `tle_fetch.py`, `risk_score.py`)

Built via IBM Bob — scoped prompt after locking weights and null-handling
in chat first. One correction made before the prompt was written: the
initial task-list research assumed CelesTrak's SATCAT exposed a
categorical RCS_SIZE field (SMALL/MEDIUM/LARGE); the actual current field
is a numeric `RCS` (m², nullable) — Space-Track's old scheme, not what
CelesTrak serves today. Prompt was corrected before Bob touched any code.

- `tle_fetch.py` — new `fetch_group_satcat()` queries CelesTrak's SATCAT
  `records.php` by the same `GROUP=` parameter and `DEBRIS_GROUPS` list
  already used for TLE fetches (3 requests total, not per-object).
  Returns a `norad_id -> rcs_m2` dict. `RCS` values that are null,
  missing, empty, or `<= 0.0` (physically meaningless) are treated as
  `None`, not zero. `get_debris_field()` joins `rcs_m2` onto every object
  before writing the cache, so cache hits carry it without a second
  SATCAT round-trip; per-group SATCAT failures are caught individually
  and logged, don't abort the TLE fetch.
- `risk_score.py` — `DEFAULT_WEIGHTS` changed from
  `{proximity: 0.6, lifetime: 0.4}` to
  `{proximity: 0.45, lifetime: 0.30, size: 0.25}`. New `_size_scores()`
  min-max normalizes `rcs_m2` only over the subset of objects that have
  a value; objects with `rcs_m2 is None` get `size_score: None` — never
  defaulted to 0 or the mean, which would have silently suppressed risk
  for exactly the untracked/small fragments this project cares about
  most. `score_debris_field()` blends all three terms when available;
  when `size_score` is `None`, blends proximity+lifetime only,
  renormalized by `(w_prox + w_life)` so output stays in `[0,1]`
  regardless of coverage. Backward-compatible: a caller-supplied
  `weights` override missing the `"size"` key still falls back to
  `DEFAULT_WEIGHTS["size"]`, same `.get()` pattern already used for
  proximity/lifetime. Every object now also carries `rcs_m2` and
  `size_score_available` (bool).

Verified: live run showed 270/274 objects with a non-null `rcs_m2` (the
4 without one were all Iridium-33 fragments). Independently re-verified
offline with synthetic data: `risk_score` stayed in `[0,1]` across
mixed-coverage batches, renormalization math correct, partial-weights
override doesn't crash, an all-null-RCS batch degrades gracefully.

Bugs found & fixed: 0 (clean addition).

## removal_method_explanation — LLM justification per technique

Modules touched: 2 (C: `main.py`, B: `optimizer.py`)

Built via IBM Bob — scoped prompt after locking the caching strategy
(per-technique, not per-object) and fallback behavior in chat first. The
`route_details` passthrough gap (see below) was caught during prompt
design, before Bob touched code, not found afterward as a bug.

- `main.py` — new `_explain_removal_method()`, cached by `removal_method`
  alone (bounded at 3 entries — only 3 distinct values exist). Calls
  Groq `openai/gpt-oss-120b` (same model as `_explain_plan`) for a 1-2
  sentence justification grounded in real flight-heritage facts
  (`net_capture` = flight-demonstrated, RemoveDEBRIS 2018-2019;
  `robotic_arm` = conceptual, no flown precedent on uncooperative
  debris; `monitor_only` = tracking, not a capture technique). The
  explanation is generic to the technique, never references a specific
  object/norad_id, since the same cached text is reused across every
  object sharing that method — an intentional choice to avoid inviting
  the LLM to hallucinate object-specific claims for no real benefit. A
  deterministic fallback template is built *before* the LLM call fires,
  so it's always ready; on any Groq error or empty/malformed response,
  the fallback is used and cached (a dead API isn't re-hit per object).
  `removal_method_explanation_source` (`"llm"` / `"fallback"`) exposes
  which happened. `_get_scored_field()` attaches both fields to every
  object.
- `optimizer.py` — `route_details` is a fixed-field dict, not a
  passthrough of the full object, so a one-line addition was required
  there too — without it, the explanation would only ever have appeared
  in `/debris-field`/`/debris/{norad_id}`, never in `/plan`/`/replan`,
  which is where "recommended method per target" (PLAN.txt) actually
  matters.

Verified: 3 new tests confirm (a) the LLM is called at most once per
distinct `removal_method` across a full live-sized batch, not once per
object, (b) `route_details` entries carry the field (proves the
`optimizer.py` wiring specifically, not just the `main.py` enrichment),
(c) a simulated Groq `APIConnectionError` produces a non-empty fallback
with `source: "fallback"`. 32/33 suite passing at the time; the 1
failure was pre-existing and unrelated (see next entry).

Bugs found & fixed: 0 (clean addition).

## TLE data-quality labeling + max_tle_age_days

Modules touched: 1 (C: `main.py`)

Built via IBM Bob — scoped prompt after locking thresholds (7/14 day,
grounded in published TLE-accuracy research) and the two-way
`max_tle_age_days` dial in chat first. One correction made during
verification, not before the prompt: a report's explanation for why the
previously-flaky test started passing ("live data rotated the object
out") was misleading — the actual fix (below) is what made it
deterministic, caught by reading the test source, not the stated reason.

- New `_data_quality(epoch_age_days)` → `"fresh"` (< 7 days), `"aging"`
  (7-14), `"stale"` (> 14). Thresholds grounded in published
  TLE-accuracy research (position error grows roughly 1-3 km/day from
  epoch; ~2 weeks is commonly cited as the outer edge of a reliable
  window). Attached unconditionally to every object in
  `_get_scored_field()`, regardless of any filter or endpoint — pure
  transparency, not gated behind a flag. This is a different kind of
  "staleness" from the existing `data_stale` cache-age flag on
  `/debris-field`: that one is about how long ago *this server* talked
  to Celestrak (2hr cycle, already fine); `data_quality` is about how
  old *that specific object's* own TLE epoch is, which re-fetching more
  often cannot fix.
- New `PlanRequest.max_tle_age_days` field (default `14.0`, inherited by
  `ReplanRequest`). `_run_plan()` excludes objects with
  `epoch_age_days > max_tle_age_days` before pool selection, same
  filtering pattern already used for `removal_method_filter`. Applies
  automatically at its default; the user can raise it (include
  older/less-trusted debris) or lower it (be stricter) in either
  direction — not a one-way tightening knob. `naive_route()` got the
  same parameter and filter, so the naive baseline and the AI route
  operate on the same data-quality window (same symmetry concern as the
  earlier RAAN/depot naive-route fix). `/debris-field` and
  `/debris/{norad_id}` deliberately never filter — they always show the
  complete field with `data_quality` labels, so a user can see what's
  old before deciding anything, independent of what threshold route
  planning is using.
- Also fixed, same commit: `test_target_norad_id_already_in_pool_no_duplicate`
  had a flawed assumption — it picked the single top-risk object
  system-wide and assumed it's always routable, which breaks whenever
  the top-risk object happens to be classified `monitor_only` (as it
  was on a live run: IRIDIUM 33 DEB, norad_id 46734). Fixed by
  restricting the top-risk pick to non-`monitor_only` objects first.
  This makes the test deterministic regardless of which object currently
  ranks highest — a prior report attributed a since-passing run to
  "live data rotated the object out," which undersold it: the fix is
  what makes it pass reliably now, not which object Celestrak happens
  to return on a given day.

Verified: 2 new tests using synthetic injected objects (real live data
can't guarantee a stale object exists at test time) confirm exclusion
at the default threshold and inclusion once the threshold is raised
past the object's age, for both `_run_plan()` and `naive_route()`.
38/38 suite passing.

Bugs found & fixed: 1 (pre-existing test assumption, see above; 0 new
bugs in the feature itself).