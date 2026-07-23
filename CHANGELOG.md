## Modules A, B, C (initial build)

Modules done: 3 (A, B, C)
- Module A — tle_fetch.py + risk_score.py. Pulls live debris positions from
  Celestrak, filters to the 700-1000km band, scores each object by risk
  (proximity + orbital lifetime).
- Module B — delta_v.py + cost_matrix.py + optimizer.py. Physics: Hohmann +
  inclination-change combined maneuver cost between any two orbits. Then an
  N×N cost matrix, then the actual OR-Tools orienteering solver — given a
  fuel budget, picks which debris to visit and in what order to maximize
  risk removed.
- Module C — main.py. Wraps A+B in a FastAPI app: GET /debris-field,
  GET /debris/{norad_id}, POST /plan, GET /naive-route (baseline for the
  naive-vs-AI comparison in the demo).

Bugs found & fixed: 2
- tle_fetch.py capped the merged debris list to 300 total after combining
  all three source clouds in fetch order, so Iridium-33 and Fengyun-1C got
  silently excluded entirely (one run came back 100% Cosmos). Fixed by
  capping each group independently before merging.
- optimizer.py — skipped_objects was computed by matching object names, but
  real debris fragments share generic names (many different objects all
  literally named "COSMOS 2251 DEB"). So skipped_count/skipped_names were
  silently wrong whenever a name collision happened between a visited and a
  skipped object. Fixed to diff by pool index instead of name, and added
  (norad_id) to display labels so route output doesn't look like the same
  object visited 15 times. Verified fixed on live server — sum check: True.

## POST /replan (new endpoint)

Wraps /plan with Groq-powered natural-language constraint parsing.
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
  (main.py), rejecting values below the safe threshold with a 422 and a
  message explaining why. Note: the threshold is pool-dependent, so this
  floor is a best-effort filter, not a guarantee — see warning field below
  for the real safety net.
- Added the same floor pattern for fuel_budget_km_s, rejecting values that
  would round to zero fuel capacity in the optimizer.
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