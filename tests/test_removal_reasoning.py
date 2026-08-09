"""
tests/test_removal_reasoning.py
================================
Tests for GET /debris/{id}/removal-methods (Removal Method Expert System).

Single engine: Groq openai/gpt-oss-20b.

Design notes
------------
All tests mock _get_scored_field() so they run instantly without Celestrak.
Groq is monkeypatched via app.main._groq_client().
_reasoning_cache is cleared per-test via monkeypatch for isolation.

Run: pytest tests/test_removal_reasoning.py -v
"""
import json
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_FAKE_FIELD: list[dict[str, Any]] = [
    {
        "norad_id": 99001,
        "name": "COSMOS DEB (99001)",
        "object_type": "fragment",
        "removal_method": "net_capture",
        "bstar": 0.000005,
        "altitude_km": 820.0,
        "inclination_deg": 74.0,
        "risk_score": 0.87,
        "epoch_age_days": 3.0,
    },
    {
        "norad_id": 99002,
        "name": "FENGYUN 1C (99002)",
        "object_type": "intact",
        "removal_method": "robotic_arm_or_net_capture",
        "bstar": 0.000001,
        "altitude_km": 850.0,
        "inclination_deg": 98.0,
        "risk_score": 0.72,
        "epoch_age_days": 5.0,
    },
    {
        "norad_id": 99003,
        "name": "IRIDIUM DEB (99003)",
        "object_type": "fragment",
        "removal_method": "monitor_only",
        "bstar": 0.0009,
        "altitude_km": 780.0,
        "inclination_deg": 86.0,
        "risk_score": 0.31,
        "epoch_age_days": 2.0,
    },
]

# Groq response with only allowlisted alternative names
_FAKE_GROQ_JSON = json.dumps({
    "reasoning": (
        "The object's moderate BSTAR indicates meaningful atmospheric drag, "
        "making net capture viable for deorbit. At 820 km altitude the orbit is "
        "within reach of current ADR mission envelopes, and net_capture is "
        "flight-demonstrated (RemoveDEBRIS 2018-2019)."
    ),
    "alternatives": [
        {
            "name": "robotic_arm",
            "why": "A robotic arm could grapple the fragment if net deployment is impractical.",
        },
        {
            "name": "monitor_only",
            "why": "If capture hardware is unavailable, continued tracking is the fallback.",
        },
    ],
})


def _make_fake_groq_client(response_text: str):
    class FakeMessage:
        content = response_text

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletion:
        choices = [FakeChoice()]

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return FakeCompletion()

    class FakeChat:
        completions = FakeCompletions()

    class FakeGroqClient:
        chat = FakeChat()

    return FakeGroqClient()


def _make_raising_groq_client(exc: Exception):
    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            raise exc

    class FakeChat:
        completions = FakeCompletions()

    class FailingGroqClient:
        chat = FakeChat()

    return FailingGroqClient()


# ---------------------------------------------------------------------------
# Test 1 — Response shape
# ---------------------------------------------------------------------------

def test_response_shape(monkeypatch):
    """Endpoint returns the documented shape; engine_used is NOT in the response."""
    monkeypatch.setattr(main_module, "_reasoning_cache", {})
    monkeypatch.setattr(main_module, "_get_scored_field", lambda **kw: _FAKE_FIELD)
    monkeypatch.setattr(main_module, "_groq_client", lambda: _make_fake_groq_client(_FAKE_GROQ_JSON))

    client = TestClient(app)
    resp = client.get("/debris/99001/removal-methods")
    assert resp.status_code == 200, resp.text

    body = resp.json()

    # Required keys present
    assert "norad_id" in body
    assert "removal_method" in body
    assert "reasoning" in body
    assert "reasoning_unavailable" in body
    assert "alternatives" in body

    # engine_used must NOT be in the response body
    assert "engine_used" not in body, (
        f"engine_used should not be exposed in the API response, got: {body}"
    )

    # Value correctness
    assert body["norad_id"] == 99001
    assert body["removal_method"] == "net_capture"
    assert body["reasoning_unavailable"] is False
    assert isinstance(body["reasoning"], str) and len(body["reasoning"]) > 0
    assert isinstance(body["alternatives"], list)
    for alt in body["alternatives"]:
        assert "name" in alt
        assert "why" in alt


# ---------------------------------------------------------------------------
# Test 2 — Content safety: no invented mass numbers or specific materials
# ---------------------------------------------------------------------------

def test_content_safety_no_invented_mass_or_material(monkeypatch):
    """Reasoning text must not contain a specific mass number or forbidden material name."""
    monkeypatch.setattr(main_module, "_reasoning_cache", {})
    monkeypatch.setattr(main_module, "_get_scored_field", lambda **kw: _FAKE_FIELD)
    monkeypatch.setattr(main_module, "_groq_client", lambda: _make_fake_groq_client(_FAKE_GROQ_JSON))

    client = TestClient(app)
    resp = client.get("/debris/99001/removal-methods")
    assert resp.status_code == 200

    reasoning = resp.json().get("reasoning", "") or ""

    # "mass" followed by a digit is forbidden
    assert not re.search(r"mass\s*[:\-]?\s*\d", reasoning, re.IGNORECASE), (
        f"Reasoning contains a specific mass number: {reasoning!r}"
    )

    # Material names not derivable from BSTAR
    forbidden = ["aluminium", "aluminum", "titanium", "steel", "kevlar",
                 "carbon fibre", "carbon fiber", "mylar"]
    for mat in forbidden:
        assert mat.lower() not in reasoning.lower(), (
            f"Reasoning contains forbidden material '{mat}': {reasoning!r}"
        )


# ---------------------------------------------------------------------------
# Test 3 — Cache: second call does not trigger a new LLM call
# ---------------------------------------------------------------------------

def test_cache_prevents_second_llm_call(monkeypatch):
    """Second call for the same norad_id is served from cache; LLM call count stays at 1."""
    monkeypatch.setattr(main_module, "_reasoning_cache", {})
    monkeypatch.setattr(main_module, "_get_scored_field", lambda **kw: _FAKE_FIELD)

    call_count = {"n": 0}

    class CountingMessage:
        content = _FAKE_GROQ_JSON

    class CountingChoice:
        message = CountingMessage()

    class CountingCompletion:
        choices = [CountingChoice()]

    class CountingCompletions:
        @staticmethod
        def create(**kwargs):
            call_count["n"] += 1
            return CountingCompletion()

    class CountingChat:
        completions = CountingCompletions()

    class CountingGroqClient:
        chat = CountingChat()

    monkeypatch.setattr(main_module, "_groq_client", lambda: CountingGroqClient())

    client = TestClient(app)

    resp1 = client.get("/debris/99001/removal-methods")
    assert resp1.status_code == 200
    assert call_count["n"] == 1, "Expected exactly 1 LLM call on first request"

    resp2 = client.get("/debris/99001/removal-methods")
    assert resp2.status_code == 200
    assert call_count["n"] == 1, (
        f"Expected 0 additional LLM calls on cache hit, got {call_count['n']} total"
    )

    assert resp1.json() == resp2.json()


# ---------------------------------------------------------------------------
# Test 4 — Groq failure → reasoning_unavailable, still 200
# ---------------------------------------------------------------------------

def test_groq_failure_returns_200_not_500(monkeypatch):
    """When Groq fails, the endpoint returns HTTP 200 with reasoning_unavailable=True
    and reasoning=None.  It must never raise a 500."""
    monkeypatch.setattr(main_module, "_reasoning_cache", {})
    monkeypatch.setattr(main_module, "_get_scored_field", lambda **kw: _FAKE_FIELD)
    monkeypatch.setattr(
        main_module,
        "_groq_client",
        lambda: _make_raising_groq_client(RuntimeError("Groq rate limit")),
    )

    client = TestClient(app)
    resp = client.get("/debris/99001/removal-methods")

    assert resp.status_code == 200, (
        f"Expected 200 on Groq failure, got {resp.status_code}: {resp.text}"
    )

    body = resp.json()
    assert body["reasoning_unavailable"] is True
    assert body["reasoning"] is None
    assert "engine_used" not in body

    # Shape still complete
    assert body["norad_id"] == 99001
    assert body["removal_method"] == "net_capture"
    assert "alternatives" in body


# ---------------------------------------------------------------------------
# Test 5 — Alternatives allowlist: hallucinated names are filtered out
# ---------------------------------------------------------------------------

def test_alternatives_only_contains_known_methods(monkeypatch):
    """LLM response containing a hallucinated method name (laser_ablation) must have
    that entry stripped; only net_capture, robotic_arm, monitor_only may appear."""
    monkeypatch.setattr(main_module, "_reasoning_cache", {})
    monkeypatch.setattr(main_module, "_get_scored_field", lambda **kw: _FAKE_FIELD)

    # Response deliberately includes two valid names and two hallucinated ones
    response_with_bad_names = json.dumps({
        "reasoning": "High BSTAR suggests a low-mass fragment; net capture is appropriate.",
        "alternatives": [
            {"name": "robotic_arm",     "why": "Valid: grapple if net fails."},
            {"name": "laser_ablation",  "why": "Invalid: hallucinated method."},
            {"name": "monitor_only",    "why": "Valid: fallback tracking."},
            {"name": "ion_beam_shepherd", "why": "Invalid: not in this system."},
        ],
    })

    monkeypatch.setattr(
        main_module, "_groq_client",
        lambda: _make_fake_groq_client(response_with_bad_names),
    )

    client = TestClient(app)
    resp = client.get("/debris/99001/removal-methods")
    assert resp.status_code == 200

    alts = resp.json()["alternatives"]
    returned_names = {a["name"] for a in alts}

    allowed = {"net_capture", "robotic_arm", "monitor_only"}
    assert returned_names <= allowed, (
        f"Alternatives contain non-allowlisted names: {returned_names - allowed}"
    )

    # Specifically confirm the two bad names were dropped
    assert "laser_ablation" not in returned_names
    assert "ion_beam_shepherd" not in returned_names

    # The two valid names should still be present
    assert "robotic_arm" in returned_names
    assert "monitor_only" in returned_names
