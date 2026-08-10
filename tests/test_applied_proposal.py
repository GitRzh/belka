"""
Tests for Part 3b: applied_proposal shortcut in /replan.

Covers the spec requirements:
  - valid applied_proposal for each of the 4 fix_types → correct new_plan,
    correct diff, _parse_overrides is NOT called (call-count check)
  - applied_proposal with out-of-bounds params → same 422 the free-text path
    would give (proves shared validation, not a lighter-weight separate path)
  - applied_proposal present + user_request_text omitted → no validation error
  - applied_proposal None + user_request_text present → existing free-text
    behavior, _parse_overrides IS called
  - applied_proposal None + user_request_text absent/empty → validation error
  - Identical new_plan: applied_proposal and equivalent free-text overrides
    produce the same plan output (proves no divergent logic between the two
    entry points)
"""
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.main as main_module
from app.main import (
    PlanRequest,
    ReplanRequest,
    _execute_overrides,
    replan,
)
from app.cost_matrix import DEFAULT_POOL_SIZE
from app.removal_method import METHOD_NET_CAPTURE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = dict(
    start_altitude_km=800.0,
    start_inclination_deg=74.0,
    fuel_budget_km_s=3.5,
    pool_size=DEFAULT_POOL_SIZE,
)


def _make_replan_req(applied_proposal=None, user_request_text="raise budget to 5", **kwargs):
    data = {**_BASE, "user_request_text": user_request_text}
    if applied_proposal is not None:
        data["applied_proposal"] = applied_proposal
    data.update(kwargs)
    return ReplanRequest(**data)


def _fake_groq_response(content: str):
    msg = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _stub_client(content: str = '{"no_changes": true}') -> MagicMock:
    """Return a _groq_client() mock whose create() returns the given JSON."""
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_groq_response(content)
    return client


# Deterministic fake _run_plan / _explain_diff / _explain_plan stubs
# so the live optimizer and Groq are never hit.

_FAKE_PLAN_A: dict[str, Any] = {
    "visited_count": 2,
    "total_fuel_cost_km_s": 1.5,
    "fuel_budget_km_s": 3.5,
    "fuel_used_fraction": 0.43,
    "total_risk_collected": 0.9,
    "pool_size_used": 40,
    "route": ["DEPOT", "DEB A (11111)", "DEB B (22222)"],
    "route_details": [
        {"norad_id": 11111, "name": "DEB A (11111)", "removal_method": "net_capture",
         "risk_score": 0.6, "delta_v_km_s": 0.8},
        {"norad_id": 22222, "name": "DEB B (22222)", "removal_method": "net_capture",
         "risk_score": 0.3, "delta_v_km_s": 0.7},
    ],
    "min_depot_hop_km_s": 0.8,
    "skipped_count": 38,
    "skipped_names": [],
    "step_breakdown": [],
    "depot": {"altitude_km": 800.0, "inclination_deg": 74.0, "raan_deg": 0.0,
              "latitude": 0.0, "longitude": 0.0},
}

_FAKE_PLAN_B: dict[str, Any] = {
    **_FAKE_PLAN_A,
    "visited_count": 3,
    "total_fuel_cost_km_s": 2.1,
    "fuel_budget_km_s": 5.0,
    "fuel_used_fraction": 0.42,
    "total_risk_collected": 1.4,
    "route": ["DEPOT", "DEB A (11111)", "DEB B (22222)", "DEB C (33333)"],
    "route_details": [
        *_FAKE_PLAN_A["route_details"],
        {"norad_id": 33333, "name": "DEB C (33333)", "removal_method": "net_capture",
         "risk_score": 0.5, "delta_v_km_s": 0.6},
    ],
}


def _patched_run_plan_factory(call_count_holder: list):
    """Returns a fake _run_plan that returns PLAN_A on the first call (old_plan)
    and PLAN_B on all subsequent calls (new_plan), tracking call count."""
    import copy
    def fake(req):
        call_count_holder[0] += 1
        if call_count_holder[0] == 1:
            return copy.deepcopy(_FAKE_PLAN_A)
        return copy.deepcopy(_FAKE_PLAN_B)
    return fake


# ===========================================================================
# ReplanRequest model validation
# ===========================================================================

class TestReplanRequestValidation:

    def test_user_request_text_only_is_valid(self):
        """Classic free-text path: no applied_proposal, text present."""
        req = ReplanRequest(**_BASE, user_request_text="increase fuel to 5")
        assert req.user_request_text == "increase fuel to 5"
        assert req.applied_proposal is None

    def test_applied_proposal_only_no_text_is_valid(self):
        """New shortcut path: applied_proposal present, text omitted."""
        req = ReplanRequest(
            **_BASE,
            applied_proposal={"fuel_budget_km_s": 5.0},
        )
        assert req.applied_proposal == {"fuel_budget_km_s": 5.0}
        assert req.user_request_text == ""

    def test_both_present_is_valid(self):
        """Both fields present — applied_proposal takes priority (no error)."""
        req = ReplanRequest(
            **_BASE,
            user_request_text="some text",
            applied_proposal={"fuel_budget_km_s": 5.0},
        )
        assert req.applied_proposal is not None

    def test_neither_raises_validation_error(self):
        """Neither field provided → model_validator raises."""
        with pytest.raises((ValidationError, ValueError)):
            ReplanRequest(**_BASE)

    def test_empty_text_no_proposal_raises(self):
        """Empty string + no applied_proposal → should fail validation."""
        with pytest.raises((ValidationError, ValueError)):
            ReplanRequest(**_BASE, user_request_text="")

    def test_whitespace_only_text_no_proposal_raises(self):
        """Whitespace-only text + no applied_proposal → should fail validation."""
        with pytest.raises((ValidationError, ValueError)):
            ReplanRequest(**_BASE, user_request_text="   ")


# ===========================================================================
# applied_proposal skips _parse_overrides (call-count check)
# ===========================================================================

class TestAppliedProposalSkipsLLM:

    def test_parse_overrides_not_called_when_applied_proposal_present(self, monkeypatch):
        """_parse_overrides must NOT be called when applied_proposal is set."""
        parse_calls: list = []

        def spy_parse(user_text, req):
            parse_calls.append(user_text)
            return {"fuel_budget_km_s": 5.0}

        monkeypatch.setattr("app.main._parse_overrides", spy_parse)
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan")

        call_count = [0]
        monkeypatch.setattr("app.main._run_plan", _patched_run_plan_factory(call_count))

        req = _make_replan_req(
            applied_proposal={"fuel_budget_km_s": 5.0},
            user_request_text="some text that should be ignored",
        )
        replan(req)

        assert parse_calls == [], (
            f"_parse_overrides was called {len(parse_calls)} time(s) "
            "but must be skipped when applied_proposal is present"
        )

    def test_parse_overrides_called_when_no_applied_proposal(self, monkeypatch):
        """_parse_overrides IS called in the normal free-text path."""
        parse_calls: list = []

        def spy_parse(user_text, req):
            parse_calls.append(user_text)
            return {"fuel_budget_km_s": 5.0}

        monkeypatch.setattr("app.main._parse_overrides", spy_parse)
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan")

        call_count = [0]
        monkeypatch.setattr("app.main._run_plan", _patched_run_plan_factory(call_count))

        req = _make_replan_req(applied_proposal=None, user_request_text="raise budget to 5")
        replan(req)

        assert len(parse_calls) == 1, (
            f"_parse_overrides should be called exactly once in the free-text path, "
            f"got {len(parse_calls)} calls"
        )


# ===========================================================================
# applied_proposal for each fix_type → correct overrides_applied + new_plan
# ===========================================================================

class TestAppliedProposalFixTypes:
    """Each fix_type that maps to a valid _execute_overrides override key
    must produce the expected overrides_applied dict and a new_plan."""

    def _run(self, monkeypatch, applied_proposal, expected_overrides_keys):
        """Shared runner: patch the pipeline, call replan, return result."""
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan")
        call_count = [0]
        monkeypatch.setattr("app.main._run_plan", _patched_run_plan_factory(call_count))

        req = _make_replan_req(applied_proposal=applied_proposal)
        result = replan(req)
        return result

    def test_budget_increase_applied(self, monkeypatch):
        result = self._run(
            monkeypatch,
            applied_proposal={"fuel_budget_km_s": 5.0},
            expected_overrides_keys={"fuel_budget_km_s"},
        )
        assert "fuel_budget_km_s" in result["overrides_applied"]
        assert result["overrides_applied"]["fuel_budget_km_s"] == 5.0
        assert "new_plan" in result
        assert "old_plan" in result
        assert "diff" in result

    def test_pool_size_increase_applied(self, monkeypatch):
        """pool_size goes via PlanRequest directly (not an _execute_overrides key),
        but the proposal dict is passed as raw_overrides — validate that passing
        it through produces a well-formed response (no 422, diff present)."""
        # pool_size is not one of the four override types Step 3 checks —
        # it passes through silently (unrecognised keys are ignored).
        # This tests that the plumbing is clean end-to-end.
        result = self._run(
            monkeypatch,
            applied_proposal={"new_pool_size": 80},   # raw proposal params key
            expected_overrides_keys=set(),             # not an _execute_overrides key
        )
        # Overrides_applied will be empty since new_pool_size isn't handled by Step 3.
        # The important check: no exception, correct structure.
        assert "new_plan" in result
        assert "diff" in result

    def test_removal_method_filter_change_applied(self, monkeypatch):
        result = self._run(
            monkeypatch,
            applied_proposal={"removal_method_filter": METHOD_NET_CAPTURE},
            expected_overrides_keys={"removal_method_filter"},
        )
        assert result["overrides_applied"].get("removal_method_filter") == METHOD_NET_CAPTURE

    def test_weights_override_applied(self, monkeypatch):
        result = self._run(
            monkeypatch,
            applied_proposal={"weights": {"proximity": 0.6, "lifetime": 0.3, "size": 0.1}},
            expected_overrides_keys={"weights"},
        )
        applied_w = result["overrides_applied"].get("weights", {})
        assert "proximity" in applied_w
        assert abs(applied_w["proximity"] + applied_w["lifetime"] + applied_w["size"] - 1.0) < 1e-5


# ===========================================================================
# Bounds validation: applied_proposal uses the SAME validation as free-text
# ===========================================================================

class TestAppliedProposalBoundsValidation:
    """Out-of-bounds values in applied_proposal must trigger the exact same
    422 HTTPException as the free-text path would — no lighter-weight path."""

    def test_out_of_bounds_budget_raises_422(self, monkeypatch):
        """fuel_budget_km_s <= 0 → 422, same as free-text path."""
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan")
        monkeypatch.setattr("app.main._run_plan", lambda req: dict(_FAKE_PLAN_A))

        req = _make_replan_req(applied_proposal={"fuel_budget_km_s": -1.0})
        with pytest.raises(HTTPException) as exc_info:
            replan(req)
        assert exc_info.value.status_code == 422

    def test_sub_minimum_budget_raises_422(self, monkeypatch):
        """fuel_budget_km_s < 0.001 → 422."""
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan")
        monkeypatch.setattr("app.main._run_plan", lambda req: dict(_FAKE_PLAN_A))

        req = _make_replan_req(applied_proposal={"fuel_budget_km_s": 0.0005})
        with pytest.raises(HTTPException) as exc_info:
            replan(req)
        assert exc_info.value.status_code == 422
        assert "0.001" in exc_info.value.detail

    def test_invalid_removal_method_filter_raises_422(self, monkeypatch):
        """removal_method_filter with an invalid value → 422."""
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan")
        monkeypatch.setattr("app.main._run_plan", lambda req: dict(_FAKE_PLAN_A))

        req = _make_replan_req(applied_proposal={"removal_method_filter": "monitor_only"})
        with pytest.raises(HTTPException) as exc_info:
            replan(req)
        assert exc_info.value.status_code == 422

    def test_out_of_range_weight_raises_422(self, monkeypatch):
        """Weight value > 1.0 → 422."""
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan")
        monkeypatch.setattr("app.main._run_plan", lambda req: dict(_FAKE_PLAN_A))

        req = _make_replan_req(
            applied_proposal={"weights": {"proximity": 2.0, "lifetime": 0.5, "size": 0.3}}
        )
        with pytest.raises(HTTPException) as exc_info:
            replan(req)
        assert exc_info.value.status_code == 422


# ===========================================================================
# Identical output: applied_proposal vs equivalent free-text parse
# ===========================================================================

class TestAppliedProposalIdenticalToFreeText:
    """The same raw_overrides dict fed into _execute_overrides directly must
    produce the same response as when it arrived via _parse_overrides().
    Proves no divergent logic between the two entry points."""

    def test_same_params_same_output(self, monkeypatch):
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan")

        import copy
        import app.main as m

        # Capture the plan results so both calls see the same deterministic data
        plan_sequence_a = [0]
        plan_sequence_b = [0]

        def fake_run_plan_a(req):
            plan_sequence_a[0] += 1
            return copy.deepcopy(_FAKE_PLAN_A if plan_sequence_a[0] == 1 else _FAKE_PLAN_B)

        def fake_run_plan_b(req):
            plan_sequence_b[0] += 1
            return copy.deepcopy(_FAKE_PLAN_A if plan_sequence_b[0] == 1 else _FAKE_PLAN_B)

        raw_overrides = {"fuel_budget_km_s": 5.0}

        # Path A: free-text parse that returns the same overrides
        monkeypatch.setattr("app.main._run_plan", fake_run_plan_a)
        monkeypatch.setattr("app.main._parse_overrides", lambda text, req: raw_overrides)
        req_free = _make_replan_req(applied_proposal=None, user_request_text="raise budget to 5")
        result_free = replan(req_free)

        # Path B: applied_proposal shortcut with the same overrides
        monkeypatch.setattr("app.main._run_plan", fake_run_plan_b)
        req_prop = _make_replan_req(applied_proposal=raw_overrides)
        result_prop = replan(req_prop)

        # The overrides_applied and new_plan stats must match
        assert result_free["overrides_applied"] == result_prop["overrides_applied"], (
            "overrides_applied diverges between free-text and applied_proposal paths"
        )
        assert result_free["new_plan"]["total_fuel_cost_km_s"] == result_prop["new_plan"]["total_fuel_cost_km_s"]
        assert result_free["new_plan"]["visited_count"] == result_prop["new_plan"]["visited_count"]
        assert result_free["diff"]["fuel_delta_km_s"] == result_prop["diff"]["fuel_delta_km_s"]
        assert result_free["diff"]["risk_delta"] == result_prop["diff"]["risk_delta"]


# ===========================================================================
# Response structure from applied_proposal path
# ===========================================================================

class TestAppliedProposalResponseShape:

    def test_response_has_all_required_top_level_keys(self, monkeypatch):
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff explanation")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan briefing")
        call_count = [0]
        monkeypatch.setattr("app.main._run_plan", _patched_run_plan_factory(call_count))

        req = _make_replan_req(applied_proposal={"fuel_budget_km_s": 5.0})
        result = replan(req)

        required_keys = {"old_plan", "new_plan", "diff", "explanation", "overrides_applied"}
        missing = required_keys - result.keys()
        assert not missing, f"Response missing top-level keys: {missing}"

    def test_diff_has_all_required_keys(self, monkeypatch):
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan")
        call_count = [0]
        monkeypatch.setattr("app.main._run_plan", _patched_run_plan_factory(call_count))

        req = _make_replan_req(applied_proposal={"fuel_budget_km_s": 5.0})
        result = replan(req)

        diff_keys = {"added", "dropped", "fuel_delta_km_s", "risk_delta", "budget_used_delta"}
        missing = diff_keys - result["diff"].keys()
        assert not missing, f"diff missing keys: {missing}"

    def test_explanation_is_string(self, monkeypatch):
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "diff narration here")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "plan briefing here")
        call_count = [0]
        monkeypatch.setattr("app.main._run_plan", _patched_run_plan_factory(call_count))

        req = _make_replan_req(applied_proposal={"fuel_budget_km_s": 5.0})
        result = replan(req)

        assert isinstance(result["explanation"], str)
        assert result["explanation"] == "diff narration here"

    def test_new_plan_has_explanation(self, monkeypatch):
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "briefing for new plan")
        call_count = [0]
        monkeypatch.setattr("app.main._run_plan", _patched_run_plan_factory(call_count))

        req = _make_replan_req(applied_proposal={"fuel_budget_km_s": 5.0})
        result = replan(req)

        assert result["new_plan"].get("explanation") == "briefing for new plan"

    def test_old_plan_has_no_explanation(self, monkeypatch):
        """old_plan never gets an explanation — design invariant."""
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "briefing")
        call_count = [0]
        monkeypatch.setattr("app.main._run_plan", _patched_run_plan_factory(call_count))

        req = _make_replan_req(applied_proposal={"fuel_budget_km_s": 5.0})
        result = replan(req)

        assert "explanation" not in result["old_plan"], (
            "old_plan must NOT have an explanation key — it's the discarded plan"
        )
