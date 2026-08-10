"""
Tests for Parts 1 and 2: Agentic Constraint Resolution (_propose_fixes).

Tests cover:
  A. Proposal shape/correctness when /plan fails (visited_count == 0).
  B. Graceful degradation: Groq failure → proposals == [], /plan still 200.
  C. Malformed LLM JSON → Layer 2 rejects, proposals == [] or partial.
  D. Out-of-bounds proposal (new_budget = 200) → Layer 3 rejects, logged.
  E. Out-of-bounds pool_size / altitude / bad method → Layer 3 rejects.
  F. Missing required field → Layer 2 structural reject.
  G. Unknown fix_type → Layer 2 structural reject.
  H. Missing params key for fix_type → Layer 2 structural reject.
  I. /plan with visited_count > 0 → no 'proposals' key at all.
  J. Layer 2+3 mixed: only passing proposals survive into response.
"""
import json
import threading
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import app.main as main_module
from app.main import (
    PlanRequest,
    _validate_proposals,
    _propose_fixes,
    plan,
    _VALID_FIX_TYPES,
    _FIX_TYPE_PARAMS_KEY,
    _VALID_PROPOSAL_METHODS,
)
from app.cost_matrix import DEFAULT_POOL_SIZE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plan_req(**kwargs) -> PlanRequest:
    defaults = dict(
        start_altitude_km=800.0,
        start_inclination_deg=74.0,
        fuel_budget_km_s=0.0001,  # impossibly tight — forces visited_count == 0
        pool_size=DEFAULT_POOL_SIZE,
    )
    defaults.update(kwargs)
    return PlanRequest(**defaults)


def _fake_groq_response(content: str):
    """Minimal object that looks like a groq ChatCompletion response."""
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def _mock_groq_with_proposals(proposals: list[dict]) -> MagicMock:
    """Return a mock _groq_client() that responds with the given proposals JSON."""
    client = MagicMock()
    payload = json.dumps({"proposals": proposals})
    client.chat.completions.create.return_value = _fake_groq_response(payload)
    return client


# A canonical valid proposal for each fix_type — used by multiple tests.
_VALID_BUDGET_PROPOSAL: dict[str, Any] = {
    "proposal": "Increase fuel budget to 5 km/s.",
    "reason": "The cheapest hop requires more delta-v than the current budget allows.",
    "fix_type": "budget_increase",
    "params": {"new_budget": 5.0},
    "estimated_impact": "Should allow the optimizer to reach the closest debris object.",
}
_VALID_POOL_PROPOSAL: dict[str, Any] = {
    "proposal": "Expand candidate pool to 80 objects.",
    "reason": "A larger pool exposes cheaper-to-reach objects that may fit the current budget.",
    "fix_type": "pool_size_increase",
    "params": {"new_pool_size": 80},
    "estimated_impact": "Higher chance of finding an affordable first hop.",
}
_VALID_ALTITUDE_PROPOSAL: dict[str, Any] = {
    "proposal": "Adjust spacecraft altitude to 750 km.",
    "reason": "Closer altitude reduces inclination-change costs to the debris band.",
    "fix_type": "altitude_expand",
    "params": {"altitude_km": 750.0},
    "estimated_impact": "Reduced depot-to-first-debris delta-v.",
}
_VALID_METHOD_PROPOSAL: dict[str, Any] = {
    "proposal": "Switch to robotic_arm_or_net_capture to expand eligible targets.",
    "reason": "The current filter restricts objects; relaxing it opens more affordable hops.",
    "fix_type": "method_filter_change",
    "params": {"removal_method": "robotic_arm_or_net_capture"},
    "estimated_impact": "More candidate objects become reachable within the current budget.",
}


# ---------------------------------------------------------------------------
# Minimal failed-route dict mirroring what _run_plan returns for v_count==0
# ---------------------------------------------------------------------------
_FAILED_ROUTE: dict[str, Any] = {
    "visited_count": 0,
    "pool_size_used": 40,
    "min_depot_hop_km_s": 0.85,
    "total_fuel_cost_km_s": 0.0,
    "fuel_budget_km_s": 0.0001,
    "fuel_used_fraction": 0.0,
    "total_risk_collected": 0.0,
    "route": [],
    "route_details": [],
    "skipped_count": 40,
    "warning": "No debris nodes were visited within the given constraints.",
    "depot": {"altitude_km": 800.0, "inclination_deg": 74.0, "raan_deg": 0.0,
              "latitude": 0.0, "longitude": 0.0},
}


# ===========================================================================
# A. Shape / correctness of surviving proposals
# ===========================================================================

class TestValidateProposalsShape:
    """_validate_proposals is pure Python — no I/O needed."""

    def test_valid_budget_proposal_passes(self):
        result = _validate_proposals([_VALID_BUDGET_PROPOSAL])
        assert len(result) == 1
        assert result[0]["fix_type"] == "budget_increase"
        assert result[0]["params"]["new_budget"] == 5.0

    def test_valid_pool_proposal_passes(self):
        result = _validate_proposals([_VALID_POOL_PROPOSAL])
        assert len(result) == 1
        assert result[0]["fix_type"] == "pool_size_increase"

    def test_valid_altitude_proposal_passes(self):
        result = _validate_proposals([_VALID_ALTITUDE_PROPOSAL])
        assert len(result) == 1
        assert result[0]["fix_type"] == "altitude_expand"

    def test_valid_method_proposal_passes(self):
        result = _validate_proposals([_VALID_METHOD_PROPOSAL])
        assert len(result) == 1
        assert result[0]["fix_type"] == "method_filter_change"

    def test_all_four_valid_proposals_pass(self):
        all_valid = [
            _VALID_BUDGET_PROPOSAL,
            _VALID_POOL_PROPOSAL,
            _VALID_ALTITUDE_PROPOSAL,
            _VALID_METHOD_PROPOSAL,
        ]
        result = _validate_proposals(all_valid)
        assert len(result) == 4

    def test_empty_list_returns_empty(self):
        assert _validate_proposals([]) == []

    def test_net_capture_method_is_valid(self):
        p = {**_VALID_METHOD_PROPOSAL, "params": {"removal_method": "net_capture"}}
        result = _validate_proposals([p])
        assert len(result) == 1

    # --- budget bounds ---
    def test_budget_at_lower_bound_passes(self):
        p = {**_VALID_BUDGET_PROPOSAL, "params": {"new_budget": 0.5}}
        assert len(_validate_proposals([p])) == 1

    def test_budget_at_upper_bound_passes(self):
        p = {**_VALID_BUDGET_PROPOSAL, "params": {"new_budget": 50.0}}
        assert len(_validate_proposals([p])) == 1

    # --- pool_size bounds ---
    def test_pool_size_at_lower_bound_passes(self):
        p = {**_VALID_POOL_PROPOSAL, "params": {"new_pool_size": 5}}
        assert len(_validate_proposals([p])) == 1

    def test_pool_size_at_upper_bound_passes(self):
        p = {**_VALID_POOL_PROPOSAL, "params": {"new_pool_size": 300}}
        assert len(_validate_proposals([p])) == 1

    # --- altitude bounds ---
    def test_altitude_at_lower_bound_passes(self):
        p = {**_VALID_ALTITUDE_PROPOSAL, "params": {"altitude_km": 500.0}}
        assert len(_validate_proposals([p])) == 1

    def test_altitude_at_upper_bound_passes(self):
        p = {**_VALID_ALTITUDE_PROPOSAL, "params": {"altitude_km": 2000.0}}
        assert len(_validate_proposals([p])) == 1


# ===========================================================================
# C/D/E/F/G/H. Layer 2 and Layer 3 rejection cases
# ===========================================================================

class TestValidateProposalsRejections:

    # --- D: out-of-bounds budget ---
    def test_budget_too_high_rejected(self):
        p = {**_VALID_BUDGET_PROPOSAL, "params": {"new_budget": 200.0}}
        assert _validate_proposals([p]) == []

    def test_budget_too_low_rejected(self):
        p = {**_VALID_BUDGET_PROPOSAL, "params": {"new_budget": 0.1}}
        assert _validate_proposals([p]) == []

    def test_budget_zero_rejected(self):
        p = {**_VALID_BUDGET_PROPOSAL, "params": {"new_budget": 0.0}}
        assert _validate_proposals([p]) == []

    # --- E: other out-of-bounds ---
    def test_pool_size_too_large_rejected(self):
        p = {**_VALID_POOL_PROPOSAL, "params": {"new_pool_size": 301}}
        assert _validate_proposals([p]) == []

    def test_pool_size_too_small_rejected(self):
        p = {**_VALID_POOL_PROPOSAL, "params": {"new_pool_size": 4}}
        assert _validate_proposals([p]) == []

    def test_altitude_too_low_rejected(self):
        p = {**_VALID_ALTITUDE_PROPOSAL, "params": {"altitude_km": 499.0}}
        assert _validate_proposals([p]) == []

    def test_altitude_too_high_rejected(self):
        p = {**_VALID_ALTITUDE_PROPOSAL, "params": {"altitude_km": 2001.0}}
        assert _validate_proposals([p]) == []

    def test_invalid_method_rejected(self):
        p = {**_VALID_METHOD_PROPOSAL, "params": {"removal_method": "laser_ablation"}}
        assert _validate_proposals([p]) == []

    def test_monitor_only_method_rejected(self):
        """monitor_only is not in _VALID_PROPOSAL_METHODS."""
        p = {**_VALID_METHOD_PROPOSAL, "params": {"removal_method": "monitor_only"}}
        assert _validate_proposals([p]) == []

    def test_harpoon_method_rejected(self):
        p = {**_VALID_METHOD_PROPOSAL, "params": {"removal_method": "harpoon_capture"}}
        assert _validate_proposals([p]) == []

    # --- G: unknown fix_type ---
    def test_unknown_fix_type_rejected(self):
        p = {**_VALID_BUDGET_PROPOSAL, "fix_type": "inclination_change"}
        assert _validate_proposals([p]) == []

    def test_invented_fix_type_rejected(self):
        p = {**_VALID_BUDGET_PROPOSAL, "fix_type": "debris_selection"}
        assert _validate_proposals([p]) == []

    # --- F: missing required field ---
    def test_missing_proposal_field_rejected(self):
        p = {k: v for k, v in _VALID_BUDGET_PROPOSAL.items() if k != "proposal"}
        assert _validate_proposals([p]) == []

    def test_missing_reason_field_rejected(self):
        p = {k: v for k, v in _VALID_BUDGET_PROPOSAL.items() if k != "reason"}
        assert _validate_proposals([p]) == []

    def test_missing_estimated_impact_rejected(self):
        p = {k: v for k, v in _VALID_BUDGET_PROPOSAL.items() if k != "estimated_impact"}
        assert _validate_proposals([p]) == []

    def test_missing_params_field_rejected(self):
        p = {k: v for k, v in _VALID_BUDGET_PROPOSAL.items() if k != "params"}
        assert _validate_proposals([p]) == []

    def test_missing_fix_type_field_rejected(self):
        p = {k: v for k, v in _VALID_BUDGET_PROPOSAL.items() if k != "fix_type"}
        assert _validate_proposals([p]) == []

    # --- H: correct fix_type but wrong params key ---
    def test_budget_increase_wrong_params_key_rejected(self):
        p = {**_VALID_BUDGET_PROPOSAL, "params": {"fuel_budget": 5.0}}  # wrong key
        assert _validate_proposals([p]) == []

    def test_pool_size_wrong_params_key_rejected(self):
        p = {**_VALID_POOL_PROPOSAL, "params": {"pool_size": 80}}  # wrong key (should be new_pool_size)
        assert _validate_proposals([p]) == []

    # --- non-dict input ---
    def test_non_dict_proposal_rejected(self):
        assert _validate_proposals(["just a string", 42, None]) == []

    # --- J: mixed valid + invalid — only valid survive ---
    def test_mixed_valid_invalid_only_valid_survives(self):
        bad_type  = {**_VALID_BUDGET_PROPOSAL, "fix_type": "invented_type"}
        bad_bound = {**_VALID_BUDGET_PROPOSAL, "params": {"new_budget": 999.0}}
        good      = _VALID_POOL_PROPOSAL
        result = _validate_proposals([bad_type, good, bad_bound])
        assert len(result) == 1
        assert result[0]["fix_type"] == "pool_size_increase"

    def test_all_bad_returns_empty(self):
        bad1 = {**_VALID_BUDGET_PROPOSAL, "fix_type": "invented"}
        bad2 = {**_VALID_POOL_PROPOSAL, "params": {"new_pool_size": 9999}}
        assert _validate_proposals([bad1, bad2]) == []


# ===========================================================================
# B. Graceful degradation: Groq failure → proposals == [], /plan still 200
# ===========================================================================

class TestProposeFixesGracefulDegradation:

    def test_groq_exception_returns_empty_list(self):
        """Any Groq exception inside _propose_fixes must return [], not raise."""
        def raise_error(**kwargs):
            raise RuntimeError("Groq is down")

        failing_client = MagicMock()
        failing_client.chat.completions.create.side_effect = raise_error

        with patch("app.main._groq_client", return_value=failing_client):
            result = _propose_fixes(_FAILED_ROUTE, _make_plan_req())

        assert result == [], f"Expected [] on Groq failure, got {result!r}"

    def test_bad_json_returns_empty_list(self):
        """Non-JSON response from LLM must be silently dropped, return []."""
        bad_client = MagicMock()
        bad_client.chat.completions.create.return_value = _fake_groq_response("not json at all !!!!")

        with patch("app.main._groq_client", return_value=bad_client):
            result = _propose_fixes(_FAILED_ROUTE, _make_plan_req())

        assert result == []

    def test_all_proposals_fail_validation_returns_empty_list(self):
        """If LLM returns valid JSON but all proposals fail validation, return []."""
        bad_proposals = [
            {**_VALID_BUDGET_PROPOSAL, "fix_type": "invented_type"},
            {**_VALID_POOL_PROPOSAL, "params": {"new_pool_size": 9999}},
        ]
        client = _mock_groq_with_proposals(bad_proposals)

        with patch("app.main._groq_client", return_value=client):
            result = _propose_fixes(_FAILED_ROUTE, _make_plan_req())

        assert result == []

    def test_empty_proposals_list_returns_empty(self):
        """LLM returns valid JSON with an empty proposals array → []."""
        client = _mock_groq_with_proposals([])
        with patch("app.main._groq_client", return_value=client):
            result = _propose_fixes(_FAILED_ROUTE, _make_plan_req())
        assert result == []

    def test_proposals_not_list_returns_empty(self):
        """proposals field is present but not a list."""
        bad_client = MagicMock()
        bad_client.chat.completions.create.return_value = _fake_groq_response(
            '{"proposals": "should be a list, not a string"}'
        )
        with patch("app.main._groq_client", return_value=bad_client):
            result = _propose_fixes(_FAILED_ROUTE, _make_plan_req())
        assert result == []


# ===========================================================================
# A + B. Integration: /plan endpoint with mocked pipeline
# ===========================================================================

# Stub dry-run result that confirms a fix is feasible (visited_count > 0).
# Used by TestPlanEndpointWithProposals to satisfy the dry-run validation layer
# without running the real optimizer.
_DRY_RUN_SUCCESS = {"visited_count": 1, "total_fuel_cost_km_s": 0.5}


class TestPlanEndpointWithProposals:
    """Test the /plan endpoint wiring — mock _run_plan so we control
    visited_count without actually running the optimizer.

    _dry_run_plan is also patched to return _DRY_RUN_SUCCESS in tests that
    expect proposals to survive — the dry-run validation layer runs inside
    _propose_fixes, and the real optimizer is never hit in unit tests."""

    def test_plan_zero_visit_includes_proposals_key(self, monkeypatch):
        """When visited_count == 0, response must have 'proposals' key."""
        monkeypatch.setattr("app.main._run_plan", lambda req: dict(_FAILED_ROUTE))
        monkeypatch.setattr("app.main._explain_plan", lambda r: None)
        monkeypatch.setattr("app.main._dry_run_plan", lambda req: dict(_DRY_RUN_SUCCESS))

        good_proposals = [_VALID_BUDGET_PROPOSAL, _VALID_POOL_PROPOSAL]
        client = _mock_groq_with_proposals(good_proposals)
        monkeypatch.setattr("app.main._groq_client", lambda: client)

        req = _make_plan_req()
        result = plan(req)

        assert "proposals" in result, "proposals key must be present when visited_count == 0"

    def test_plan_zero_visit_proposals_shape(self, monkeypatch):
        """Every returned proposal must have all five required fields."""
        monkeypatch.setattr("app.main._run_plan", lambda req: dict(_FAILED_ROUTE))
        monkeypatch.setattr("app.main._explain_plan", lambda r: None)
        monkeypatch.setattr("app.main._dry_run_plan", lambda req: dict(_DRY_RUN_SUCCESS))

        good_proposals = [_VALID_BUDGET_PROPOSAL, _VALID_POOL_PROPOSAL]
        client = _mock_groq_with_proposals(good_proposals)
        monkeypatch.setattr("app.main._groq_client", lambda: client)

        req = _make_plan_req()
        result = plan(req)

        required = {"proposal", "reason", "fix_type", "params", "estimated_impact"}
        for p in result["proposals"]:
            missing = required - p.keys()
            assert not missing, f"Proposal missing fields {missing}: {p}"

    def test_plan_zero_visit_proposals_within_bounds(self, monkeypatch):
        """All proposals in response must have valid fix_type and in-bounds params."""
        monkeypatch.setattr("app.main._run_plan", lambda req: dict(_FAILED_ROUTE))
        monkeypatch.setattr("app.main._explain_plan", lambda r: None)
        monkeypatch.setattr("app.main._dry_run_plan", lambda req: dict(_DRY_RUN_SUCCESS))

        good_proposals = [_VALID_BUDGET_PROPOSAL, _VALID_POOL_PROPOSAL, _VALID_ALTITUDE_PROPOSAL]
        client = _mock_groq_with_proposals(good_proposals)
        monkeypatch.setattr("app.main._groq_client", lambda: client)

        req = _make_plan_req()
        result = plan(req)

        for p in result["proposals"]:
            assert p["fix_type"] in _VALID_FIX_TYPES, f"Invalid fix_type: {p['fix_type']}"

    def test_plan_groq_failure_still_returns_200_with_empty_proposals(self, monkeypatch):
        """Groq failure must not block /plan — route returns, proposals == []."""
        monkeypatch.setattr("app.main._run_plan", lambda req: dict(_FAILED_ROUTE))
        monkeypatch.setattr("app.main._explain_plan", lambda r: None)
        # _dry_run_plan is never reached when LLM fails; no patch needed here.

        failing_client = MagicMock()
        failing_client.chat.completions.create.side_effect = RuntimeError("connection reset")
        monkeypatch.setattr("app.main._groq_client", lambda: failing_client)

        req = _make_plan_req()
        result = plan(req)  # must not raise

        assert result["visited_count"] == 0
        assert "proposals" in result
        assert result["proposals"] == []

    def test_plan_zero_visit_out_of_bounds_proposal_excluded(self, monkeypatch):
        """An out-of-bounds proposal (new_budget=200) must be excluded from response.
        The valid pool_size_increase proposal must still survive the dry-run."""
        monkeypatch.setattr("app.main._run_plan", lambda req: dict(_FAILED_ROUTE))
        monkeypatch.setattr("app.main._explain_plan", lambda r: None)
        monkeypatch.setattr("app.main._dry_run_plan", lambda req: dict(_DRY_RUN_SUCCESS))

        bad_budget = {**_VALID_BUDGET_PROPOSAL, "params": {"new_budget": 200.0}}
        good_pool  = _VALID_POOL_PROPOSAL
        client = _mock_groq_with_proposals([bad_budget, good_pool])
        monkeypatch.setattr("app.main._groq_client", lambda: client)

        req = _make_plan_req()
        result = plan(req)

        fix_types_in_response = [p["fix_type"] for p in result["proposals"]]
        assert "pool_size_increase" in fix_types_in_response, "Valid proposal should survive"
        for p in result["proposals"]:
            if p["fix_type"] == "budget_increase":
                assert p["params"]["new_budget"] <= 50, "budget=200 must have been dropped"

    def test_plan_successful_route_no_proposals_key(self, monkeypatch):
        """When visited_count > 0 (successful plan), 'proposals' must NOT be present."""
        success_result = dict(_FAILED_ROUTE)
        success_result["visited_count"] = 3
        success_result["total_risk_collected"] = 1.5
        success_result["route_details"] = [
            {"norad_id": 1, "name": "DEB A (1)", "removal_method": "net_capture",
             "risk_score": 0.5, "delta_v_km_s": 0.3}
        ]
        monkeypatch.setattr("app.main._run_plan", lambda req: success_result)
        monkeypatch.setattr("app.main._explain_plan", lambda r: "Stub briefing.")

        req = _make_plan_req(fuel_budget_km_s=10.0)
        result = plan(req)

        assert "proposals" not in result, (
            "proposals key must NOT appear when the plan has visited_count > 0"
        )

    def test_plan_malformed_json_proposals_empty_not_500(self, monkeypatch):
        """Malformed LLM JSON must not raise — proposals == [], route intact.
        _dry_run_plan is never reached when JSON parsing fails; no patch needed."""
        monkeypatch.setattr("app.main._run_plan", lambda req: dict(_FAILED_ROUTE))
        monkeypatch.setattr("app.main._explain_plan", lambda r: None)

        bad_client = MagicMock()
        bad_client.chat.completions.create.return_value = _fake_groq_response("{broken json}")
        monkeypatch.setattr("app.main._groq_client", lambda: bad_client)

        req = _make_plan_req()
        result = plan(req)

        assert result["visited_count"] == 0
        assert result["proposals"] == []

    def test_plan_zero_visit_proposals_never_blocks_route(self, monkeypatch):
        """Route fields (visited_count, warning, pool_size_used) always returned
        regardless of what happens inside _propose_fixes.
        _propose_fixes is fully mocked here — no LLM or dry-run hits."""
        monkeypatch.setattr("app.main._run_plan", lambda req: dict(_FAILED_ROUTE))
        monkeypatch.setattr("app.main._explain_plan", lambda r: None)
        monkeypatch.setattr("app.main._propose_fixes", lambda route_result, req: [])

        req = _make_plan_req()
        result = plan(req)

        assert "visited_count" in result
        assert "warning" in result
        assert "pool_size_used" in result
        assert result["proposals"] == []


# ===========================================================================
# Constants / allowlist integrity
# ===========================================================================

class TestAllowlistIntegrity:
    """Validate the constants themselves so no typo silently breaks validation."""

    def test_valid_fix_types_has_four_entries(self):
        assert len(_VALID_FIX_TYPES) == 4

    def test_fix_type_params_key_covers_all_fix_types(self):
        assert set(_FIX_TYPE_PARAMS_KEY.keys()) == _VALID_FIX_TYPES

    def test_valid_proposal_methods_contains_only_allowed_values(self):
        assert "net_capture" in _VALID_PROPOSAL_METHODS
        assert "robotic_arm_or_net_capture" in _VALID_PROPOSAL_METHODS
        assert "monitor_only" not in _VALID_PROPOSAL_METHODS
        assert "laser_ablation" not in _VALID_PROPOSAL_METHODS
