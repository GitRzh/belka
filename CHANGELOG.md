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