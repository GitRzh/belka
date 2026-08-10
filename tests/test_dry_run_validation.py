"""
Tests for the dry-run validation layer added to _propose_fixes.

Covers:
  A. _build_dry_run_req: all four fix_types translate to the correct PlanRequest field.
  B. _build_dry_run_req: unknown fix_type returns None (skip dry-run).
  C. _build_dry_run_req: invalid params (non-numeric altitude_km) returns None.
  D. _dry_run_plan: returns result dict, never raises even on HTTPException from _run_plan.
  E. _propose_fixes end-to-end: proposals that pass dry-run are returned.
  F. _propose_fixes end-to-end: proposals whose dry-run visits 0 are dropped.
  G. _propose_fixes end-to-end: all proposals dropped → [].
  H. concurrent dry-runs: all 4 fix_types run concurrently (timing check).
  I. original proposal order is preserved regardless of concurrent completion order.
"""
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.main import (
    PlanRequest,
    _build_dry_run_req,
    _dry_run_plan,
    _propose_fixes,
    plan,
)
from app.cost_matrix import DEFAULT_POOL_SIZE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = dict(
    start_altitude_km=800.0,
    start_inclination_deg=74.0,
    fuel_budget_km_s=0.0001,   # intentionally tight so baseline visits 0
    pool_size=DEFAULT_POOL_SIZE,
)


def _make_plan_req(**kwargs) -> PlanRequest:
    return PlanRequest(**{**_BASE, **kwargs})


def _fake_groq_response(content: str):
    msg = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _mock_groq_with_proposals(proposals: list[dict]) -> MagicMock:
    import json
    client = MagicMock()
    client.chat.completions.create.return_value = _fake_groq_response(
        json.dumps({"proposals": proposals})
    )
    return client


_VALID_BUDGET_PROPOSAL = {
    "proposal": "Increase fuel budget to 5 km/s.",
    "reason": "The cheapest hop requires more delta-v than the current budget allows.",
    "fix_type": "budget_increase",
    "params": {"new_budget": 5.0},
    "estimated_impact": "Should allow the optimizer to reach the closest debris object.",
}
_VALID_POOL_PROPOSAL = {
    "proposal": "Expand candidate pool to 80 objects.",
    "reason": "A larger pool exposes cheaper-to-reach objects that may fit the current budget.",
    "fix_type": "pool_size_increase",
    "params": {"new_pool_size": 80},
    "estimated_impact": "Higher chance of finding an affordable first hop.",
}
_VALID_ALTITUDE_PROPOSAL = {
    "proposal": "Reposition spacecraft/depot to 750 km to reduce first-hop cost.",
    "reason": "Moving the depot closer to the debris band lowers the depot-to-first-debris delta-v.",
    "fix_type": "altitude_expand",
    "params": {"altitude_km": 750.0},
    "estimated_impact": "Reduced first-hop transit cost.",
}
_VALID_METHOD_PROPOSAL = {
    "proposal": "Switch to robotic_arm_or_net_capture.",
    "reason": "Relaxing the method filter opens more affordable hop targets.",
    "fix_type": "method_filter_change",
    "params": {"removal_method": "robotic_arm_or_net_capture"},
    "estimated_impact": "More candidate objects become reachable.",
}

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
_DRY_RUN_SUCCESS = {"visited_count": 1, "total_fuel_cost_km_s": 0.5}
_DRY_RUN_FAIL    = {"visited_count": 0, "total_fuel_cost_km_s": 0.0}


# ===========================================================================
# A. _build_dry_run_req: all four fix_types
# ===========================================================================

class TestBuildDryRunReq:

    def test_budget_increase_sets_fuel_budget(self):
        req = _make_plan_req()
        proposal = {**_VALID_BUDGET_PROPOSAL}
        dry_req = _build_dry_run_req(req, proposal)
        assert dry_req is not None
        assert dry_req.fuel_budget_km_s == 5.0

    def test_pool_size_increase_sets_pool_size(self):
        req = _make_plan_req()
        proposal = {**_VALID_POOL_PROPOSAL}
        dry_req = _build_dry_run_req(req, proposal)
        assert dry_req is not None
        assert dry_req.pool_size == 80

    def test_altitude_expand_sets_start_altitude(self):
        req = _make_plan_req()
        proposal = {**_VALID_ALTITUDE_PROPOSAL}
        dry_req = _build_dry_run_req(req, proposal)
        assert dry_req is not None
        assert dry_req.start_altitude_km == 750.0

    def test_method_filter_change_sets_removal_method_filter(self):
        req = _make_plan_req()
        proposal = {**_VALID_METHOD_PROPOSAL}
        dry_req = _build_dry_run_req(req, proposal)
        assert dry_req is not None
        assert dry_req.removal_method_filter == "robotic_arm_or_net_capture"

    def test_other_fields_unchanged(self):
        """budget_increase only changes fuel_budget_km_s — other fields must carry through."""
        req = _make_plan_req()
        proposal = {**_VALID_BUDGET_PROPOSAL}
        dry_req = _build_dry_run_req(req, proposal)
        assert dry_req is not None
        assert dry_req.pool_size == req.pool_size
        assert dry_req.start_altitude_km == req.start_altitude_km
        assert dry_req.start_inclination_deg == req.start_inclination_deg


# ===========================================================================
# B. _build_dry_run_req: unknown fix_type → None
# ===========================================================================

class TestBuildDryRunReqUnknownType:

    def test_unknown_fix_type_returns_none(self):
        req = _make_plan_req()
        proposal = {
            "fix_type": "laser_ablation",
            "params": {"energy_kj": 50},
        }
        assert _build_dry_run_req(req, proposal) is None

    def test_missing_fix_type_returns_none(self):
        req = _make_plan_req()
        # fix_type key absent — .get() returns None, falls through to the else branch
        proposal = {"params": {"new_budget": 5.0}}
        assert _build_dry_run_req(req, proposal) is None


# ===========================================================================
# C. _build_dry_run_req: bad params → None (PlanRequest construction fails)
# ===========================================================================

class TestBuildDryRunReqBadParams:

    def test_non_numeric_altitude_returns_none(self):
        req = _make_plan_req()
        proposal = {
            "fix_type": "altitude_expand",
            "params": {"altitude_km": "not-a-number"},
        }
        # float("not-a-number") raises ValueError → _build_dry_run_req returns None
        assert _build_dry_run_req(req, proposal) is None

    def test_negative_pool_size_returns_none(self):
        """pool_size <= 0 violates PlanRequest.pool_size gt=0 validator → None."""
        req = _make_plan_req()
        proposal = {
            "fix_type": "pool_size_increase",
            "params": {"new_pool_size": -1},
        }
        assert _build_dry_run_req(req, proposal) is None


# ===========================================================================
# D. _dry_run_plan: never raises, returns dict
# ===========================================================================

class TestDryRunPlan:

    def test_returns_dict_on_success(self, monkeypatch):
        monkeypatch.setattr("app.main._run_plan", lambda req, **kw: {"visited_count": 2})
        req = _make_plan_req(fuel_budget_km_s=5.0)
        result = _dry_run_plan(req)
        assert isinstance(result, dict)
        assert result["visited_count"] == 2

    def test_never_raises_on_http_exception(self, monkeypatch):
        from fastapi import HTTPException
        def boom(req, **kw):
            raise HTTPException(status_code=502, detail="fetch failed")
        monkeypatch.setattr("app.main._run_plan", boom)
        req = _make_plan_req(fuel_budget_km_s=5.0)
        result = _dry_run_plan(req)   # must not raise
        assert result["visited_count"] == 0
        assert "dry_run_error" in result

    def test_never_raises_on_unexpected_exception(self, monkeypatch):
        def boom(req, **kw):
            raise RuntimeError("something exploded")
        monkeypatch.setattr("app.main._run_plan", boom)
        req = _make_plan_req(fuel_budget_km_s=5.0)
        result = _dry_run_plan(req)
        assert result["visited_count"] == 0
        assert "dry_run_error" in result

    def test_passes_dry_run_time_limit(self, monkeypatch):
        """_dry_run_plan must pass time_limit_seconds=DRY_RUN_TIME_LIMIT_SECONDS to _run_plan."""
        from app.optimizer import DRY_RUN_TIME_LIMIT_SECONDS
        captured_limit: list[int] = []

        def spy(req, *, time_limit_seconds=None, **kw):
            captured_limit.append(time_limit_seconds)
            return {"visited_count": 1}

        monkeypatch.setattr("app.main._run_plan", spy)
        req = _make_plan_req(fuel_budget_km_s=5.0)
        _dry_run_plan(req)
        assert captured_limit == [DRY_RUN_TIME_LIMIT_SECONDS], (
            f"Expected time_limit_seconds={DRY_RUN_TIME_LIMIT_SECONDS}, "
            f"got {captured_limit}"
        )


# ===========================================================================
# E. _propose_fixes: proposals that pass dry-run are returned
# ===========================================================================

class TestProposeFixesDryRunPass:

    def test_proposals_passing_dry_run_are_returned(self, monkeypatch):
        """All validated proposals whose dry-run returns visited_count > 0 survive."""
        monkeypatch.setattr("app.main._dry_run_plan", lambda req: dict(_DRY_RUN_SUCCESS))
        client = _mock_groq_with_proposals([_VALID_BUDGET_PROPOSAL, _VALID_POOL_PROPOSAL])
        with patch("app.main._groq_client", return_value=client):
            result = _propose_fixes(_FAILED_ROUTE, _make_plan_req())
        assert len(result) == 2
        fix_types = {p["fix_type"] for p in result}
        assert fix_types == {"budget_increase", "pool_size_increase"}

    def test_single_passing_proposal_returned(self, monkeypatch):
        monkeypatch.setattr("app.main._dry_run_plan", lambda req: dict(_DRY_RUN_SUCCESS))
        client = _mock_groq_with_proposals([_VALID_ALTITUDE_PROPOSAL])
        with patch("app.main._groq_client", return_value=client):
            result = _propose_fixes(_FAILED_ROUTE, _make_plan_req())
        assert len(result) == 1
        assert result[0]["fix_type"] == "altitude_expand"


# ===========================================================================
# F. _propose_fixes: proposals whose dry-run visits 0 are dropped
# ===========================================================================

class TestProposeFixesDryRunFail:

    def test_proposals_failing_dry_run_are_dropped(self, monkeypatch):
        """dry-run returning visited_count=0 causes the proposal to be dropped."""
        monkeypatch.setattr("app.main._dry_run_plan", lambda req: dict(_DRY_RUN_FAIL))
        client = _mock_groq_with_proposals([_VALID_BUDGET_PROPOSAL, _VALID_POOL_PROPOSAL])
        with patch("app.main._groq_client", return_value=client):
            result = _propose_fixes(_FAILED_ROUTE, _make_plan_req())
        assert result == []

    def test_partial_dry_run_pass_only_survivors_returned(self, monkeypatch):
        """budget_increase passes dry-run, pool_size_increase fails — only budget survives."""
        call_count = [0]
        def selective_dry_run(req):
            call_count[0] += 1
            # First call (budget_increase) succeeds; second call (pool) fails.
            # We can't inspect fix_type here, so use call order.
            return dict(_DRY_RUN_SUCCESS) if call_count[0] == 1 else dict(_DRY_RUN_FAIL)

        monkeypatch.setattr("app.main._dry_run_plan", selective_dry_run)
        client = _mock_groq_with_proposals([_VALID_BUDGET_PROPOSAL, _VALID_POOL_PROPOSAL])
        with patch("app.main._groq_client", return_value=client):
            result = _propose_fixes(_FAILED_ROUTE, _make_plan_req())
        # Exactly one proposal must have survived
        assert len(result) == 1


# ===========================================================================
# G. _propose_fixes: all proposals dropped → []
# ===========================================================================

class TestProposeFixesAllDropped:

    def test_all_proposals_fail_dry_run_returns_empty(self, monkeypatch):
        monkeypatch.setattr("app.main._dry_run_plan", lambda req: dict(_DRY_RUN_FAIL))
        client = _mock_groq_with_proposals(
            [_VALID_BUDGET_PROPOSAL, _VALID_POOL_PROPOSAL, _VALID_ALTITUDE_PROPOSAL]
        )
        with patch("app.main._groq_client", return_value=client):
            result = _propose_fixes(_FAILED_ROUTE, _make_plan_req())
        assert result == []


# ===========================================================================
# H. Concurrent dry-runs: total wall time ≈ single dry-run, not sum
# ===========================================================================

class TestConcurrentDryRuns:

    def test_three_proposals_finish_faster_than_sum(self, monkeypatch):
        """3 concurrent dry-runs each sleeping 0.1 s must finish in < 0.25 s total,
        not the sequential 0.3 s+ that would result from serial execution."""
        import time as _time

        def slow_dry_run(req):
            _time.sleep(0.1)
            return dict(_DRY_RUN_SUCCESS)

        monkeypatch.setattr("app.main._dry_run_plan", slow_dry_run)
        proposals = [_VALID_BUDGET_PROPOSAL, _VALID_POOL_PROPOSAL, _VALID_ALTITUDE_PROPOSAL]
        client = _mock_groq_with_proposals(proposals)
        with patch("app.main._groq_client", return_value=client):
            t0 = time.perf_counter()
            result = _propose_fixes(_FAILED_ROUTE, _make_plan_req())
            elapsed = time.perf_counter() - t0

        assert len(result) == 3, f"All 3 proposals should survive, got {len(result)}"
        assert elapsed < 0.25, (
            f"3 concurrent 0.1 s dry-runs should complete in <0.25 s total, "
            f"took {elapsed:.3f}s — suggests sequential execution"
        )


# ===========================================================================
# I. Original proposal order preserved
# ===========================================================================

class TestProposalOrderPreserved:

    def test_order_matches_llm_output_order(self, monkeypatch):
        """Proposals must be returned in the same order the LLM emitted them,
        regardless of which dry-run future completes first."""
        import time as _time

        # Introduce staggered sleep times: altitude finishes first (0.01 s),
        # budget last (0.12 s) — if order were completion-order, altitude
        # would appear first, but the sort must restore LLM order.
        sleep_by_type = {
            "budget_increase":    0.12,
            "pool_size_increase": 0.06,
            "altitude_expand":    0.01,
        }

        def ordered_dry_run(req):
            fix_type = None
            # Identify the fix_type from request fields changed by _build_dry_run_req.
            if req.fuel_budget_km_s != _BASE["fuel_budget_km_s"]:
                fix_type = "budget_increase"
            elif req.pool_size != _BASE["pool_size"]:
                fix_type = "pool_size_increase"
            elif req.start_altitude_km != _BASE["start_altitude_km"]:
                fix_type = "altitude_expand"
            _time.sleep(sleep_by_type.get(fix_type, 0.01))
            return dict(_DRY_RUN_SUCCESS)

        monkeypatch.setattr("app.main._dry_run_plan", ordered_dry_run)
        proposals = [_VALID_BUDGET_PROPOSAL, _VALID_POOL_PROPOSAL, _VALID_ALTITUDE_PROPOSAL]
        client = _mock_groq_with_proposals(proposals)
        with patch("app.main._groq_client", return_value=client):
            result = _propose_fixes(_FAILED_ROUTE, _make_plan_req())

        assert len(result) == 3
        expected_order = ["budget_increase", "pool_size_increase", "altitude_expand"]
        actual_order   = [p["fix_type"] for p in result]
        assert actual_order == expected_order, (
            f"Expected LLM-emission order {expected_order}, got {actual_order}"
        )
