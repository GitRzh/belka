"""
Unit tests for _parse_overrides in main.py.

All three cases are exercised with a fully mocked Groq client — no network
calls, no API key required:

1. First call returns invalid JSON, second returns valid JSON → retry fires,
   result is the parsed valid dict (stray keys filtered by allowlist).
2. Both calls return invalid JSON → ValueError raised with the specific
   "malformed JSON after retry" message.
3. First call returns valid JSON → succeeds immediately, retry never fires.

The mock patches app.main._groq_client so the real Groq constructor (which
requires GROQ_API_KEY) is never reached.
"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

from app.main import _parse_overrides, PlanRequest
from app.optimizer import RISK_PENALTY_SCALE
from app.cost_matrix import DEFAULT_POOL_SIZE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_req(**kwargs) -> PlanRequest:
    """Minimal PlanRequest with sensible defaults for the prompt context."""
    defaults = dict(
        start_altitude_km=800.0,
        start_inclination_deg=74.0,
        fuel_budget_km_s=3.5,
        pool_size=DEFAULT_POOL_SIZE,
        risk_penalty_scale=RISK_PENALTY_SCALE,
        weights=None,
    )
    defaults.update(kwargs)
    return PlanRequest(**defaults)


def _fake_response(content: str):
    """Build a minimal object that looks like a groq ChatCompletion response."""
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def _mock_client_with_responses(*contents: str) -> MagicMock:
    """Return a mock _groq_client() whose .chat.completions.create() yields
    each content string in turn on successive calls."""
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _fake_response(c) for c in contents
    ]
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestParseOverrides(unittest.TestCase):

    def test_retry_fires_and_succeeds_on_second_call(self):
        """First response is invalid JSON; second is valid → retry succeeds."""
        valid_json = '{"fuel_budget_km_s": 1.75}'
        mock_client = _mock_client_with_responses("NOT VALID JSON {{{{", valid_json)

        with patch("app.main._groq_client", return_value=mock_client):
            result = _parse_overrides("cut the fuel budget in half", _make_req())

        # Confirm the retry actually fired — create() must have been called twice
        self.assertEqual(mock_client.chat.completions.create.call_count, 2,
                         "create() should be called exactly twice (attempt 0 + retry)")

        # Confirm the valid parse came through correctly
        self.assertEqual(result, {"fuel_budget_km_s": 1.75})

    def test_both_calls_invalid_raises_specific_valueerror(self):
        """Both responses are invalid JSON → ValueError with the exact message."""
        mock_client = _mock_client_with_responses("oops {", "still broken }")

        with patch("app.main._groq_client", return_value=mock_client):
            with self.assertRaises(ValueError) as ctx:
                _parse_overrides("cut the fuel budget in half", _make_req())

        self.assertEqual(mock_client.chat.completions.create.call_count, 2,
                         "create() should still be called twice before giving up")
        self.assertIn("malformed JSON after retry", str(ctx.exception),
                      "ValueError message should mention 'malformed JSON after retry'")
        self.assertIn("still broken }", str(ctx.exception),
                      "ValueError message should include the raw bad response")

    def test_first_call_valid_no_retry(self):
        """First response is valid JSON → succeeds on attempt 0, no retry."""
        valid_json = '{"risk_penalty_scale": 9000.0, "intruder_key": "ignored"}'
        mock_client = _mock_client_with_responses(valid_json)

        with patch("app.main._groq_client", return_value=mock_client):
            result = _parse_overrides("raise the risk penalty", _make_req())

        self.assertEqual(mock_client.chat.completions.create.call_count, 1,
                         "create() should only be called once when first response is valid")

        # Allowlist filter must have stripped the stray key
        self.assertIn("risk_penalty_scale", result)
        self.assertNotIn("intruder_key", result,
                         "stray keys outside _ALLOWED_OVERRIDE_KEYS must be filtered")
        self.assertEqual(result["risk_penalty_scale"], 9000.0)

    def test_no_changes_key_passes_through_allowlist(self):
        """no_changes is in _ALLOWED_OVERRIDE_KEYS and must not be stripped."""
        mock_client = _mock_client_with_responses('{"no_changes": true}')

        with patch("app.main._groq_client", return_value=mock_client):
            result = _parse_overrides("make it look cooler", _make_req())

        self.assertEqual(result, {"no_changes": True})


if __name__ == "__main__":
    unittest.main()
