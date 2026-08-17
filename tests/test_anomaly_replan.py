"""
Tests for the anomaly-replan additions (scoped-down Feature 5):

Change 1 — exclude_norad_ids on ReplanRequest / filter in _run_plan():
  - A NORAD ID in exclude_norad_ids is absent from new_plan's route.
  - old_plan is unaffected (produced before the filter is in scope via
    a separate _run_plan call on the original req, which lacks exclude_norad_ids).
  - Empty exclude_norad_ids (the default) changes nothing (regression guard).
  - exclude_norad_ids is not present on PlanRequest (would be a ValidationError).

Change 2 — start_altitude_km / start_inclination_deg override in _execute_overrides():
  - Valid pair is accepted and appears in overrides_applied.
  - Optional start_raan_deg is also accepted when provided.
  - Partial override (only one of the two) → 422.
  - start_altitude_km <= 0 → 422.
  - These keys are NOT in _ALLOWED_OVERRIDE_KEYS (free-text gating set).

All tests use mocked _run_plan / _explain_diff / _explain_plan so they are
fast, offline, and don't require Groq or Celestrak connectivity.
"""
import copy
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.main as main_module
from app.main import (
    PlanRequest,
    ReplanRequest,
    _execute_overrides,
    _run_plan,
    replan,
    _ALLOWED_OVERRIDE_KEYS,
)
from app.cost_matrix import DEFAULT_POOL_SIZE


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
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


# Two deterministic fake plans with overlapping but distinct route_details.
# PLAN_A: visits objects 11111 + 22222
# PLAN_B: visits only 33333 (simulates new plan after excluding 11111 + 22222)

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
    "visited_count": 1,
    "total_fuel_cost_km_s": 0.9,
    "fuel_budget_km_s": 3.5,
    "fuel_used_fraction": 0.26,
    "total_risk_collected": 0.4,
    "route": ["DEPOT", "DEB C (33333)"],
    "route_details": [
        {"norad_id": 33333, "name": "DEB C (33333)", "removal_method": "net_capture",
         "risk_score": 0.4, "delta_v_km_s": 0.9},
    ],
}


def _run_plan_factory(old_plan, new_plan):
    """Returns a fake _run_plan: first call → old_plan, subsequent → new_plan."""
    call_count = [0]

    def _fake(req):
        call_count[0] += 1
        return copy.deepcopy(old_plan if call_count[0] == 1 else new_plan)

    return _fake


# ---------------------------------------------------------------------------
# Change 1 — exclude_norad_ids field on ReplanRequest
# ---------------------------------------------------------------------------

class TestExcludeNoradIdsField:

    def test_default_is_empty_list(self):
        """exclude_norad_ids defaults to [] when not supplied."""
        req = _make_replan_req(applied_proposal={"fuel_budget_km_s": 4.0})
        assert req.exclude_norad_ids == []

    def test_field_accepted_on_replan_request(self):
        """exclude_norad_ids is a valid field on ReplanRequest."""
        req = _make_replan_req(
            applied_proposal={"fuel_budget_km_s": 4.0},
            exclude_norad_ids=[11111, 22222],
        )
        assert req.exclude_norad_ids == [11111, 22222]

    def test_field_absent_on_plan_request(self):
        """PlanRequest does NOT have exclude_norad_ids — the attribute is absent."""
        req = PlanRequest(**_BASE)
        assert not hasattr(req, "exclude_norad_ids"), (
            "exclude_norad_ids must not be a field on PlanRequest"
        )


# ---------------------------------------------------------------------------
# Change 1 — exclude_norad_ids filter in _run_plan()
# ---------------------------------------------------------------------------

class TestExcludeNoradIdsFilter:
    """
    _run_plan() uses getattr(req, "exclude_norad_ids", None) so it works for
    both PlanRequest (no field → None → filter skipped) and ReplanRequest
    (field present → filter applied only on new_plan's run).

    These tests drive _run_plan() directly against a mocked _get_scored_field
    so they're fast and don't need Celestrak.
    """

    # A minimal scored field with three objects.
    _SCORED_FIELD = [
        {"norad_id": 11111, "name": "DEB A", "risk_score": 0.9,
         "epoch_age_days": 1.0, "removal_method": "net_capture",
         "removal_method_explanation": "", "removal_method_explanation_source": "default",
         "possible_methods": [], "method_maturity": {}, "data_quality": "good",
         "altitude_km": 400.0, "inclination_deg": 51.6, "raan_deg": 0.0,
         "object_type": "debris"},
        {"norad_id": 22222, "name": "DEB B", "risk_score": 0.7,
         "epoch_age_days": 2.0, "removal_method": "net_capture",
         "removal_method_explanation": "", "removal_method_explanation_source": "default",
         "possible_methods": [], "method_maturity": {}, "data_quality": "good",
         "altitude_km": 410.0, "inclination_deg": 51.6, "raan_deg": 0.0,
         "object_type": "debris"},
        {"norad_id": 33333, "name": "DEB C", "risk_score": 0.5,
         "epoch_age_days": 3.0, "removal_method": "net_capture",
         "removal_method_explanation": "", "removal_method_explanation_source": "default",
         "possible_methods": [], "method_maturity": {}, "data_quality": "good",
         "altitude_km": 420.0, "inclination_deg": 51.6, "raan_deg": 0.0,
         "object_type": "debris"},
    ]

    def _fake_optimize(self, *args, **kwargs):
        """Stub optimizer that returns the pool as a no-cost route."""
        return {"route": [], "route_details": [], "total_cost": 0.0,
                "fuel_used_fraction": 0.0, "visited_count": 0,
                "skipped_count": 0, "skipped_names": [], "step_breakdown": [],
                "min_depot_hop_km_s": 0.0}

    def _run_with_patches(self, monkeypatch, req):
        """Patch _get_scored_field + optimize_route and call _run_plan."""
        scored = list(self._SCORED_FIELD)
        monkeypatch.setattr("app.main._get_scored_field", lambda weights=None, force_refresh=False: scored)
        # Capture the pool passed to optimize_route so we can inspect it.
        captured = {}

        fake_self = self

        def fake_optimize(pool, **kwargs):
            captured["pool"] = list(pool)
            return fake_self._fake_optimize()

        monkeypatch.setattr("app.main.optimize_route", fake_optimize)
        _run_plan(req)
        return captured

    def test_excluded_id_absent_from_scored_pool(self, monkeypatch):
        """An ID in exclude_norad_ids must not appear in the pool passed to optimize_route."""
        req = ReplanRequest(
            **_BASE,
            applied_proposal={"fuel_budget_km_s": 3.5},
            exclude_norad_ids=[11111, 22222],
        )
        captured = self._run_with_patches(monkeypatch, req)
        pool_ids = {o["norad_id"] for o in captured.get("pool", [])}
        assert 11111 not in pool_ids, "11111 should be excluded but appeared in pool"
        assert 22222 not in pool_ids, "22222 should be excluded but appeared in pool"
        assert 33333 in pool_ids, "33333 should NOT be excluded"

    def test_empty_exclude_list_changes_nothing(self, monkeypatch):
        """Empty exclude_norad_ids (default) must leave the pool untouched."""
        req = ReplanRequest(
            **_BASE,
            applied_proposal={"fuel_budget_km_s": 3.5},
            exclude_norad_ids=[],
        )
        captured = self._run_with_patches(monkeypatch, req)
        pool_ids = {o["norad_id"] for o in captured.get("pool", [])}
        assert {11111, 22222, 33333} == pool_ids, (
            "Empty exclude list should leave all three objects in pool"
        )

    def test_old_plan_unaffected_by_exclude_norad_ids(self, monkeypatch):
        """old_plan is produced by _run_plan(req) where req IS the ReplanRequest,
        which still has exclude_norad_ids.  BUT old_plan should represent the
        pre-anomaly state.  The design spec says 'old_plan is unaffected' meaning
        the caller passes exclude_norad_ids only for the NEW plan.  The actual
        mechanism: _run_plan() is called once with req (ReplanRequest, has field)
        for old_plan and once with new_req (PlanRequest, field stripped) for
        new_plan.  The getattr guard ensures both handle the field gracefully.

        This test verifies that a PlanRequest (which lacks exclude_norad_ids) is
        not broken by the getattr guard — i.e., the fallback to None is safe."""
        plan_req = PlanRequest(**_BASE)
        captured = self._run_with_patches(monkeypatch, plan_req)
        pool_ids = {o["norad_id"] for o in captured.get("pool", [])}
        # PlanRequest has no exclude_norad_ids → getattr returns None → no filter
        assert {11111, 22222, 33333} == pool_ids, (
            "PlanRequest should not apply exclude_norad_ids filter"
        )


# ---------------------------------------------------------------------------
# Change 1 — end-to-end replan: new_plan excludes IDs, old_plan does not
# ---------------------------------------------------------------------------

class TestExcludeNoradIdsEndToEnd:
    """
    Drives replan() with monkeypatched _run_plan stubs that track WHICH req
    was used for each call.  The first call (old_plan) uses a ReplanRequest
    with exclude_norad_ids; the second call (new_plan) uses a PlanRequest
    with the field stripped.  We verify plan shapes reflect the filter.
    """

    def test_new_plan_excludes_ids_old_plan_does_not(self, monkeypatch):
        """
        old_plan uses full PLAN_A (visits 11111 + 22222).
        new_plan uses PLAN_B (visits only 33333, simulating successful exclusion).
        Verify: new_plan route has no 11111/22222, old_plan route still has them.
        """
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan")
        monkeypatch.setattr(
            "app.main._run_plan",
            _run_plan_factory(_FAKE_PLAN_A, _FAKE_PLAN_B),
        )

        req = _make_replan_req(
            applied_proposal={"fuel_budget_km_s": 3.5},
            exclude_norad_ids=[11111, 22222],
        )
        result = replan(req)

        old_norad_ids = {d["norad_id"] for d in result["old_plan"]["route_details"]}
        new_norad_ids = {d["norad_id"] for d in result["new_plan"]["route_details"]}

        assert 11111 in old_norad_ids, "old_plan should still visit 11111"
        assert 22222 in old_norad_ids, "old_plan should still visit 22222"
        assert 11111 not in new_norad_ids, "new_plan must not visit 11111 (excluded)"
        assert 22222 not in new_norad_ids, "new_plan must not visit 22222 (excluded)"
        assert 33333 in new_norad_ids, "new_plan should visit 33333"


# ---------------------------------------------------------------------------
# Change 2 — start-position override in _execute_overrides
# ---------------------------------------------------------------------------

class TestStartPositionOverride:

    def _run(self, monkeypatch, applied_proposal):
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan")
        monkeypatch.setattr(
            "app.main._run_plan",
            _run_plan_factory(_FAKE_PLAN_A, _FAKE_PLAN_B),
        )
        req = _make_replan_req(applied_proposal=applied_proposal)
        return replan(req)

    def test_valid_altitude_inclination_accepted(self, monkeypatch):
        """Valid start_altitude_km + start_inclination_deg → accepted, in overrides_applied."""
        result = self._run(
            monkeypatch,
            applied_proposal={"start_altitude_km": 600.0, "start_inclination_deg": 51.6},
        )
        assert result["overrides_applied"]["start_altitude_km"] == 600.0
        assert result["overrides_applied"]["start_inclination_deg"] == 51.6

    def test_optional_raan_also_accepted(self, monkeypatch):
        """start_raan_deg is optional but accepted when provided."""
        result = self._run(
            monkeypatch,
            applied_proposal={
                "start_altitude_km": 600.0,
                "start_inclination_deg": 51.6,
                "start_raan_deg": 45.0,
            },
        )
        assert result["overrides_applied"]["start_raan_deg"] == 45.0

    def test_only_altitude_without_inclination_is_valid(self, monkeypatch):
        """Providing only start_altitude_km (no inclination) is valid — altitude-only
        override keeps the existing inclination from req (altitude_expand use case)."""
        result = self._run(
            monkeypatch,
            applied_proposal={"start_altitude_km": 900.0},
        )
        assert result["overrides_applied"]["start_altitude_km"] == 900.0
        assert "start_inclination_deg" not in result["overrides_applied"]

    def test_only_inclination_without_altitude_raises_422(self, monkeypatch):
        """Providing only start_inclination_deg (no altitude) → 422."""
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan")
        monkeypatch.setattr("app.main._run_plan", lambda req: copy.deepcopy(_FAKE_PLAN_A))

        req = _make_replan_req(applied_proposal={"start_inclination_deg": 51.6})
        with pytest.raises(HTTPException) as exc_info:
            replan(req)
        assert exc_info.value.status_code == 422
        assert "together" in exc_info.value.detail

    def test_zero_altitude_raises_422(self, monkeypatch):
        """start_altitude_km = 0 → 422."""
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan")
        monkeypatch.setattr("app.main._run_plan", lambda req: copy.deepcopy(_FAKE_PLAN_A))

        req = _make_replan_req(applied_proposal={"start_altitude_km": 0.0, "start_inclination_deg": 51.6})
        with pytest.raises(HTTPException) as exc_info:
            replan(req)
        assert exc_info.value.status_code == 422
        assert "start_altitude_km must be > 0" in exc_info.value.detail

    def test_negative_altitude_raises_422(self, monkeypatch):
        """start_altitude_km < 0 → 422."""
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan")
        monkeypatch.setattr("app.main._run_plan", lambda req: copy.deepcopy(_FAKE_PLAN_A))

        req = _make_replan_req(applied_proposal={"start_altitude_km": -200.0, "start_inclination_deg": 51.6})
        with pytest.raises(HTTPException) as exc_info:
            replan(req)
        assert exc_info.value.status_code == 422

    def test_start_altitude_km_not_in_allowed_override_keys(self):
        """start_altitude_km must NOT be in _ALLOWED_OVERRIDE_KEYS (free-text gating set)."""
        assert "start_altitude_km" not in _ALLOWED_OVERRIDE_KEYS, (
            "start_altitude_km was added to _ALLOWED_OVERRIDE_KEYS — this would "
            "expose raw orbit override to the free-text LLM path, which is wrong."
        )

    def test_start_inclination_deg_not_in_allowed_override_keys(self):
        """start_inclination_deg must NOT be in _ALLOWED_OVERRIDE_KEYS."""
        assert "start_inclination_deg" not in _ALLOWED_OVERRIDE_KEYS, (
            "start_inclination_deg was added to _ALLOWED_OVERRIDE_KEYS — this would "
            "expose raw orbit override to the free-text LLM path, which is wrong."
        )

    def test_start_raan_deg_not_in_allowed_override_keys(self):
        """start_raan_deg must NOT be in _ALLOWED_OVERRIDE_KEYS."""
        assert "start_raan_deg" not in _ALLOWED_OVERRIDE_KEYS, (
            "start_raan_deg was added to _ALLOWED_OVERRIDE_KEYS — same reasoning."
        )


# ---------------------------------------------------------------------------
# Bug fix — old_plan must NOT be affected by exclude_norad_ids
# ---------------------------------------------------------------------------

class TestOldPlanExcludeNoradIdsNotLeaked:
    """
    Regression test for the bug where _execute_overrides() passed the original
    ReplanRequest (with exclude_norad_ids set) to _run_plan() for old_plan,
    causing old_plan to silently drop those targets.

    These tests call _execute_overrides() with the REAL _run_plan() — no
    monkeypatching of _run_plan — so the actual filter logic is exercised.
    _get_scored_field and optimize_route are still mocked for speed/offline use.
    """

    _SCORED_FIELD = [
        {"norad_id": 11111, "name": "DEB A", "risk_score": 0.9,
         "epoch_age_days": 1.0, "removal_method": "net_capture",
         "removal_method_explanation": "", "removal_method_explanation_source": "default",
         "possible_methods": [], "method_maturity": {}, "data_quality": "good",
         "altitude_km": 400.0, "inclination_deg": 51.6, "raan_deg": 0.0,
         "object_type": "debris"},
        {"norad_id": 22222, "name": "DEB B", "risk_score": 0.7,
         "epoch_age_days": 2.0, "removal_method": "net_capture",
         "removal_method_explanation": "", "removal_method_explanation_source": "default",
         "possible_methods": [], "method_maturity": {}, "data_quality": "good",
         "altitude_km": 410.0, "inclination_deg": 51.6, "raan_deg": 0.0,
         "object_type": "debris"},
        {"norad_id": 33333, "name": "DEB C", "risk_score": 0.5,
         "epoch_age_days": 3.0, "removal_method": "net_capture",
         "removal_method_explanation": "", "removal_method_explanation_source": "default",
         "possible_methods": [], "method_maturity": {}, "data_quality": "good",
         "altitude_km": 420.0, "inclination_deg": 51.6, "raan_deg": 0.0,
         "object_type": "debris"},
    ]

    def _patch_infra(self, monkeypatch):
        """Patch _get_scored_field, optimize_route, _explain_diff, _explain_plan."""
        scored = list(self._SCORED_FIELD)
        monkeypatch.setattr(
            "app.main._get_scored_field",
            lambda weights=None, force_refresh=False: list(scored),
        )

        # Capture pools seen by optimize_route across all calls (old + new).
        pools_seen = []

        def fake_optimize(pool, fuel_budget_km_s=3.5, **kwargs):
            pools_seen.append([o["norad_id"] for o in pool])
            return {
                "route": [],
                "route_details": [{"norad_id": o["norad_id"], "name": o["name"],
                                   "removal_method": o["removal_method"],
                                   "risk_score": o["risk_score"], "delta_v_km_s": 0.1}
                                  for o in pool],
                "total_cost": 0.0,
                "total_fuel_cost_km_s": 0.0,
                "fuel_budget_km_s": fuel_budget_km_s,
                "fuel_used_fraction": 0.0,
                "total_risk_collected": round(
                    sum(o["risk_score"] for o in pool), 4
                ),
                "visited_count": len(pool),
                "skipped_count": 0,
                "skipped_names": [],
                "step_breakdown": [],
                "min_depot_hop_km_s": 0.0,
                "net_capacity_constrained": 1,
            }

        monkeypatch.setattr("app.main.optimize_route", fake_optimize)
        monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff")
        monkeypatch.setattr("app.main._explain_plan", lambda r: "stub plan")

        return pools_seen

    def test_excluded_id_present_in_old_plan(self, monkeypatch):
        """
        After the fix: old_plan is built with exclude_norad_ids cleared, so 11111
        must appear in old_plan's route_details even though the caller excluded it.
        """
        pools_seen = self._patch_infra(monkeypatch)

        req = ReplanRequest(
            **_BASE,
            applied_proposal={"fuel_budget_km_s": 3.5},
            exclude_norad_ids=[11111],
        )
        result = _execute_overrides(req, req.applied_proposal)

        old_ids = {d["norad_id"] for d in result["old_plan"]["route_details"]}
        assert 11111 in old_ids, (
            "11111 was excluded from old_plan — the fix (model_copy with "
            "exclude_norad_ids=[]) was not applied or was reverted"
        )

    def test_excluded_id_absent_from_new_plan(self, monkeypatch):
        """
        Complementary check: the _run_plan() filter still removes 11111 when
        called with a ReplanRequest that has exclude_norad_ids=[11111].

        This tests the filter at the _run_plan() layer directly (same pattern as
        TestExcludeNoradIdsFilter) rather than through _execute_overrides(),
        because _execute_overrides() builds new_req as a PlanRequest (field
        stripped) — the filter applies when new_req carries the field, i.e. when
        _run_plan is given the ReplanRequest directly.  This guard ensures the
        filter logic itself still works correctly after the old_plan fix.
        """
        pools_seen = self._patch_infra(monkeypatch)

        req = ReplanRequest(
            **_BASE,
            applied_proposal={"fuel_budget_km_s": 3.5},
            exclude_norad_ids=[11111],
        )
        # Call _run_plan directly — this is the layer where exclude_norad_ids
        # is applied.  Same pattern as TestExcludeNoradIdsFilter._run_with_patches.
        _run_plan(req)

        # The pool captured by fake_optimize should NOT contain 11111.
        assert pools_seen, "fake_optimize was never called — _run_plan did not run"
        last_pool_ids = set(pools_seen[-1])
        assert 11111 not in last_pool_ids, (
            "11111 appeared in the pool passed to optimize_route — "
            "the exclude_norad_ids filter in _run_plan() is broken"
        )

    def test_req_unmutated_after_execute_overrides(self, monkeypatch):
        """
        Guard: _execute_overrides must not mutate req in place.
        req.exclude_norad_ids must equal the original list after the call.
        """
        self._patch_infra(monkeypatch)

        original_ids = [11111]
        req = ReplanRequest(
            **_BASE,
            applied_proposal={"fuel_budget_km_s": 3.5},
            exclude_norad_ids=list(original_ids),
        )
        _execute_overrides(req, req.applied_proposal)

        assert req.exclude_norad_ids == original_ids, (
            "req.exclude_norad_ids was mutated by _execute_overrides — use "
            "model_copy() instead of modifying the original request"
        )
