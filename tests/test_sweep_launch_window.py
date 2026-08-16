"""
pytest suite for Feature 4: Launch-Window Pareto Explorer.

Tests cover:
  - compute_pareto_frontier() — both modes, error exclusion, tie-breaking
  - POST /sweep-launch-window — response shape, sweep_mode branching,
    forced_target_ids triggers single_axis and does NOT call _run_plan,
    narration caching, launch_date on PlanRequest (Guardrails 1-3)
"""
from typing import Any
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import hashlib
import json

import pytest

from app.optimizer import compute_pareto_frontier
import app.main as main_module
from app.main import (
    PlanRequest,
    SweepLaunchWindowRequest,
    sweep_launch_window,
    _sweep_narration_cache,
    _sweep_cache_key,
    _MAX_LAUNCH_DAY_OFFSET,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEFAULT_KWARGS: dict[str, Any] = dict(
    start_altitude_km=800.0,
    start_inclination_deg=74.0,
    fuel_budget_km_s=2.5,
)


def _make_plan_req(**kwargs) -> PlanRequest:
    return PlanRequest(**{**DEFAULT_KWARGS, **kwargs})


def _make_sweep_req(**kwargs) -> SweepLaunchWindowRequest:
    return SweepLaunchWindowRequest(**{**DEFAULT_KWARGS, **kwargs})


def _fake_groq_client():
    class FakeMessage:
        content = "Stub sweep narration."
    class FakeChoice:
        message = FakeMessage()
    class FakeResp:
        choices = [FakeChoice()]
    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            return FakeResp()
    class FakeChat:
        completions = FakeCompletions()
    class FakeClient:
        chat = FakeChat()
    return FakeClient()


# ---------------------------------------------------------------------------
# compute_pareto_frontier — unit tests (pure function, no I/O)
# ---------------------------------------------------------------------------

class TestComputeParetoFrontierSingleAxis:
    """forced=True → single_axis mode."""

    def test_sweep_mode_is_single_axis(self):
        results = [
            {"day_offset": 0.0, "total_fuel_cost_km_s": 2.0, "total_risk_collected": 10.0},
            {"day_offset": 1.0, "total_fuel_cost_km_s": 1.5, "total_risk_collected": 10.0},
        ]
        _, mode = compute_pareto_frontier(results, forced=True)
        assert mode == "single_axis"

    def test_lowest_fuel_marked_optimal(self):
        results = [
            {"day_offset": 0.0, "total_fuel_cost_km_s": 2.0, "total_risk_collected": 10.0},
            {"day_offset": 1.0, "total_fuel_cost_km_s": 1.5, "total_risk_collected": 10.0},
            {"day_offset": 2.0, "total_fuel_cost_km_s": 3.0, "total_risk_collected": 10.0},
        ]
        annotated, _ = compute_pareto_frontier(results, forced=True)
        optimal = [r for r in annotated if r["is_pareto_optimal"]]
        assert len(optimal) == 1
        assert optimal[0]["day_offset"] == 1.0

    def test_tie_broken_by_lower_day_offset(self):
        results = [
            {"day_offset": 0.0, "total_fuel_cost_km_s": 1.5, "total_risk_collected": 10.0},
            {"day_offset": 1.0, "total_fuel_cost_km_s": 1.5, "total_risk_collected": 10.0},
        ]
        annotated, _ = compute_pareto_frontier(results, forced=True)
        optimal = [r for r in annotated if r["is_pareto_optimal"]]
        assert len(optimal) == 1
        assert optimal[0]["day_offset"] == 0.0

    def test_error_results_excluded_and_marked_false(self):
        results = [
            {"day_offset": 0.0, "error": "Solver failed"},
            {"day_offset": 1.0, "total_fuel_cost_km_s": 1.5, "total_risk_collected": 10.0},
        ]
        annotated, _ = compute_pareto_frontier(results, forced=True)
        error_entries = [r for r in annotated if "error" in r]
        assert len(error_entries) == 1
        assert error_entries[0]["is_pareto_optimal"] is False

    def test_all_errors_no_optimal(self):
        results = [
            {"day_offset": 0.0, "error": "fail1"},
            {"day_offset": 1.0, "error": "fail2"},
        ]
        annotated, _ = compute_pareto_frontier(results, forced=True)
        assert all(not r["is_pareto_optimal"] for r in annotated)

    def test_single_result_is_optimal(self):
        results = [
            {"day_offset": 0.0, "total_fuel_cost_km_s": 1.0, "total_risk_collected": 5.0},
        ]
        annotated, _ = compute_pareto_frontier(results, forced=True)
        assert annotated[0]["is_pareto_optimal"] is True

    def test_original_dicts_not_mutated(self):
        """compute_pareto_frontier must not mutate its input dicts."""
        r = {"day_offset": 0.0, "total_fuel_cost_km_s": 1.5, "total_risk_collected": 10.0}
        results = [r]
        compute_pareto_frontier(results, forced=True)
        assert "is_pareto_optimal" not in r


class TestComputeParetoFrontierParetoMode:
    """forced=False → pareto_frontier mode."""

    def test_sweep_mode_is_pareto_frontier(self):
        results = [
            {"day_offset": 0.0, "total_fuel_cost_km_s": 2.0, "total_risk_collected": 10.0},
        ]
        _, mode = compute_pareto_frontier(results, forced=False)
        assert mode == "pareto_frontier"

    def test_dominated_point_not_optimal(self):
        # Day 1 is strictly better on both axes than day 0 → day 0 is dominated.
        results = [
            {"day_offset": 0.0, "total_fuel_cost_km_s": 3.0, "total_risk_collected": 5.0},
            {"day_offset": 1.0, "total_fuel_cost_km_s": 2.0, "total_risk_collected": 8.0},
        ]
        annotated, _ = compute_pareto_frontier(results, forced=False)
        d0 = next(r for r in annotated if r["day_offset"] == 0.0)
        d1 = next(r for r in annotated if r["day_offset"] == 1.0)
        assert d0["is_pareto_optimal"] is False
        assert d1["is_pareto_optimal"] is True

    def test_trade_off_points_both_optimal(self):
        # Day 0: lower fuel, lower risk. Day 1: higher fuel, higher risk.
        # Neither dominates the other → both are Pareto-optimal.
        results = [
            {"day_offset": 0.0, "total_fuel_cost_km_s": 1.0, "total_risk_collected": 5.0},
            {"day_offset": 1.0, "total_fuel_cost_km_s": 2.0, "total_risk_collected": 10.0},
        ]
        annotated, _ = compute_pareto_frontier(results, forced=False)
        assert all(r["is_pareto_optimal"] for r in annotated)

    def test_all_dominated_by_one(self):
        # Day 2 dominates all others → only day 2 is optimal.
        results = [
            {"day_offset": 0.0, "total_fuel_cost_km_s": 3.0, "total_risk_collected": 5.0},
            {"day_offset": 1.0, "total_fuel_cost_km_s": 2.5, "total_risk_collected": 6.0},
            {"day_offset": 2.0, "total_fuel_cost_km_s": 1.0, "total_risk_collected": 10.0},
        ]
        annotated, _ = compute_pareto_frontier(results, forced=False)
        optimal = [r for r in annotated if r["is_pareto_optimal"]]
        assert len(optimal) == 1
        assert optimal[0]["day_offset"] == 2.0

    def test_equal_on_both_axes_both_optimal(self):
        # Identical fuel and risk — neither dominates (no strict inequality).
        results = [
            {"day_offset": 0.0, "total_fuel_cost_km_s": 2.0, "total_risk_collected": 8.0},
            {"day_offset": 1.0, "total_fuel_cost_km_s": 2.0, "total_risk_collected": 8.0},
        ]
        annotated, _ = compute_pareto_frontier(results, forced=False)
        assert all(r["is_pareto_optimal"] for r in annotated)

    def test_error_results_excluded(self):
        results = [
            {"day_offset": 0.0, "error": "fail"},
            {"day_offset": 1.0, "total_fuel_cost_km_s": 2.0, "total_risk_collected": 8.0},
        ]
        annotated, _ = compute_pareto_frontier(results, forced=False)
        error_entry = next(r for r in annotated if "error" in r)
        valid_entry = next(r for r in annotated if "error" not in r)
        assert error_entry["is_pareto_optimal"] is False
        assert valid_entry["is_pareto_optimal"] is True

    def test_output_length_equals_input_length(self):
        results = [
            {"day_offset": float(i), "total_fuel_cost_km_s": float(i), "total_risk_collected": float(10 - i)}
            for i in range(5)
        ]
        annotated, _ = compute_pareto_frontier(results, forced=False)
        assert len(annotated) == len(results)


# ---------------------------------------------------------------------------
# Sweep endpoint — shape and branch tests
# ---------------------------------------------------------------------------

def _stub_run_plan_result(raan_used=None):
    """Return a minimal _run_plan-shaped result dict."""
    return {
        "total_fuel_cost_km_s": 1.8 + (raan_used or 0) * 0.001,
        "total_risk_collected": 12.3,
        "visited_count": 5,
        "route_details": [
            {"data_quality": "fresh", "norad_id": 1},
        ],
        "pool_size_used": 40,
        "depot": {"altitude_km": 800.0, "inclination_deg": 74.0, "raan_deg": 0.0, "latitude": 0.0, "longitude": 0.0},
    }


def _stub_forced_route_result():
    return {
        "total_fuel_cost_km_s": 1.5,
        "total_risk_collected": 8.0,
        "visited_count": 3,
        "route_details": [{"data_quality": "aging", "norad_id": 10}],
        "nets_carried_required": 1,
        "step_breakdown": [],
        "total_fuel_saved_km_s": 0.0,
    }


class TestSweepEndpointShape:
    """Basic response shape and field presence."""

    def test_returns_sweep_mode_and_window(self, monkeypatch):
        _sweep_narration_cache.clear()
        monkeypatch.setattr("app.main._groq_client", lambda: _fake_groq_client())
        monkeypatch.setattr("app.main._run_plan", lambda req, **kw: _stub_run_plan_result())

        req = _make_sweep_req(window_days=2)
        result = sweep_launch_window(req)

        assert "sweep_mode" in result
        assert "window" in result
        assert "lowest_fuel_date" in result
        assert "narration" in result
        assert "echo" in result

    def test_window_has_correct_length_coarse(self, monkeypatch):
        """window should have at least window_days+1 entries (coarse sweep)."""
        _sweep_narration_cache.clear()
        monkeypatch.setattr("app.main._groq_client", lambda: _fake_groq_client())
        monkeypatch.setattr("app.main._run_plan", lambda req, **kw: _stub_run_plan_result())

        req = _make_sweep_req(window_days=3)
        result = sweep_launch_window(req)

        # At minimum 4 entries (days 0-3) + possible refined entries.
        assert len(result["window"]) >= 4

    def test_each_window_entry_has_required_fields(self, monkeypatch):
        _sweep_narration_cache.clear()
        monkeypatch.setattr("app.main._groq_client", lambda: _fake_groq_client())
        monkeypatch.setattr("app.main._run_plan", lambda req, **kw: _stub_run_plan_result())

        req = _make_sweep_req(window_days=2)
        result = sweep_launch_window(req)

        for entry in result["window"]:
            if "error" in entry:
                continue
            assert "day_offset" in entry
            assert "launch_date" in entry
            assert "total_fuel_cost_km_s" in entry
            assert "total_risk_collected" in entry
            assert "is_pareto_optimal" in entry
            assert "data_quality" in entry
            assert "visited_count" in entry

    def test_day_offset_is_float(self, monkeypatch):
        _sweep_narration_cache.clear()
        monkeypatch.setattr("app.main._groq_client", lambda: _fake_groq_client())
        monkeypatch.setattr("app.main._run_plan", lambda req, **kw: _stub_run_plan_result())

        req = _make_sweep_req(window_days=2)
        result = sweep_launch_window(req)

        for entry in result["window"]:
            assert isinstance(entry["day_offset"], float), (
                f"day_offset must be float, got {type(entry['day_offset'])} on {entry}"
            )

    def test_sweep_mode_pareto_frontier_when_no_forced_ids(self, monkeypatch):
        _sweep_narration_cache.clear()
        monkeypatch.setattr("app.main._groq_client", lambda: _fake_groq_client())
        monkeypatch.setattr("app.main._run_plan", lambda req, **kw: _stub_run_plan_result())

        req = _make_sweep_req(window_days=2)
        result = sweep_launch_window(req)
        assert result["sweep_mode"] == "pareto_frontier"

    def test_lowest_fuel_date_present(self, monkeypatch):
        _sweep_narration_cache.clear()
        monkeypatch.setattr("app.main._groq_client", lambda: _fake_groq_client())
        monkeypatch.setattr("app.main._run_plan", lambda req, **kw: _stub_run_plan_result())

        req = _make_sweep_req(window_days=2)
        result = sweep_launch_window(req)
        lfd = result["lowest_fuel_date"]
        assert lfd is not None
        assert "day_offset" in lfd
        assert "launch_date" in lfd

    def test_echo_contains_required_fields(self, monkeypatch):
        _sweep_narration_cache.clear()
        monkeypatch.setattr("app.main._groq_client", lambda: _fake_groq_client())
        monkeypatch.setattr("app.main._run_plan", lambda req, **kw: _stub_run_plan_result())

        req = _make_sweep_req(window_days=2)
        result = sweep_launch_window(req)
        echo = result["echo"]
        assert "start_position" in echo
        assert "weights" in echo
        assert "window_days" in echo
        assert "forced_target_ids" in echo


class TestSweepSingleAxisBranch:
    """forced_target_ids present → single_axis, solve_forced_route called, NOT _run_plan."""

    def _get_forced_target_ids(self, scored_field):
        """Pull 2 real norad_ids from the scored field for use as forced targets."""
        return [o["norad_id"] for o in scored_field[:2]]

    def test_single_axis_mode_when_forced_ids_present(self, monkeypatch):
        _sweep_narration_cache.clear()
        monkeypatch.setattr("app.main._groq_client", lambda: _fake_groq_client())

        call_log = []

        def fake_forced_route(targets, start_altitude_km, start_inclination_deg,
                              start_raan_deg=0.0, fuel_budget_km_s=None,
                              max_wait_days=0.0, min_saving_km_s=0.0):
            call_log.append("forced")
            return _stub_forced_route_result()

        def fake_run_plan(req, **kw):
            call_log.append("run_plan")
            return _stub_run_plan_result()

        # Monkeypatch both; get real norad IDs from the field.
        scored = main_module._get_scored_field()
        forced_ids = [o["norad_id"] for o in scored[:2]]

        monkeypatch.setattr("app.main.solve_forced_route", fake_forced_route)
        monkeypatch.setattr("app.main._run_plan", fake_run_plan)

        req = _make_sweep_req(window_days=2, forced_target_ids=forced_ids)
        result = sweep_launch_window(req)

        assert result["sweep_mode"] == "single_axis"
        # solve_forced_route must have been called; _run_plan must NOT.
        assert "forced" in call_log, "solve_forced_route was never called"
        assert "run_plan" not in call_log, "_run_plan was called in forced branch (must not be)"

    def test_forced_ids_single_optimal_marked(self, monkeypatch):
        """In single_axis mode, exactly one entry is is_pareto_optimal."""
        _sweep_narration_cache.clear()
        monkeypatch.setattr("app.main._groq_client", lambda: _fake_groq_client())

        call_count = [0]
        def fake_forced_route(targets, start_altitude_km, start_inclination_deg,
                              start_raan_deg=0.0, fuel_budget_km_s=None,
                              max_wait_days=0.0, min_saving_km_s=0.0):
            call_count[0] += 1
            # Vary fuel slightly so a clear minimum exists.
            return {**_stub_forced_route_result(), "total_fuel_cost_km_s": 1.5 + call_count[0] * 0.1}

        scored = main_module._get_scored_field()
        forced_ids = [o["norad_id"] for o in scored[:2]]
        monkeypatch.setattr("app.main.solve_forced_route", fake_forced_route)

        req = _make_sweep_req(window_days=3, forced_target_ids=forced_ids)
        result = sweep_launch_window(req)

        valid_window = [r for r in result["window"] if "error" not in r]
        optimal = [r for r in valid_window if r["is_pareto_optimal"]]
        assert len(optimal) == 1


class TestSweepNarrationCaching:
    def test_narration_cached_on_second_call(self, monkeypatch):
        _sweep_narration_cache.clear()
        call_log = []

        def fake_groq():
            class FakeMsg:
                content = "Cached narration stub."
            class FakeChoice:
                message = FakeMsg()
            class FakeResp:
                choices = [FakeChoice()]
            class FakeCompletions:
                @staticmethod
                def create(**kwargs):
                    call_log.append("llm")
                    return FakeResp()
            class FakeChat:
                completions = FakeCompletions()
            class FakeClient:
                chat = FakeChat()
            return FakeClient()

        monkeypatch.setattr("app.main._groq_client", fake_groq)
        monkeypatch.setattr("app.main._run_plan", lambda req, **kw: _stub_run_plan_result())

        req = _make_sweep_req(window_days=1)
        result1 = sweep_launch_window(req)
        calls_after_first = len(call_log)

        result2 = sweep_launch_window(req)
        calls_after_second = len(call_log)

        assert result1["narration"] == result2["narration"]
        # No new LLM narration call on the second request.
        assert calls_after_second == calls_after_first

    def test_cache_key_differs_on_different_window_days(self):
        req1 = _make_sweep_req(window_days=7)
        req2 = _make_sweep_req(window_days=14)
        assert _sweep_cache_key(req1) != _sweep_cache_key(req2)

    def test_cache_key_differs_on_different_forced_ids(self):
        req1 = _make_sweep_req(forced_target_ids=None)
        req2 = _make_sweep_req(forced_target_ids=[12345])
        assert _sweep_cache_key(req1) != _sweep_cache_key(req2)

    def test_narration_none_on_llm_failure(self, monkeypatch):
        _sweep_narration_cache.clear()

        def fail_explain(*args, **kwargs):
            return None

        monkeypatch.setattr("app.main._explain_sweep", fail_explain)
        monkeypatch.setattr("app.main._run_plan", lambda req, **kw: _stub_run_plan_result())

        req = _make_sweep_req(window_days=1)
        result = sweep_launch_window(req)
        assert result["narration"] is None
        assert len(result["window"]) >= 2  # optimizer runs still succeed


# ---------------------------------------------------------------------------
# PlanRequest.launch_date — Guardrail tests
# ---------------------------------------------------------------------------

class TestLaunchDateGuardrails:
    """Guardrail 1: absent → no change. Guardrail 2: epoch anchor. Guardrail 3: 14-day cap."""

    def test_guardrail1_absent_launch_date_zero_change(self, monkeypatch):
        """No launch_date → raan_deg echoed back unchanged."""
        req = _make_plan_req(start_raan_deg=45.0)
        result = main_module._run_plan(req)
        # raan in depot should be 45.0 (no drift applied).
        assert result["depot"]["raan_deg"] == 45.0

    def test_guardrail1_launch_date_none_is_same_as_absent(self, monkeypatch):
        req_no_ld = _make_plan_req(start_raan_deg=30.0)
        req_none  = _make_plan_req(start_raan_deg=30.0, launch_date=None)
        r1 = main_module._run_plan(req_no_ld)
        r2 = main_module._run_plan(req_none)
        assert r1["depot"]["raan_deg"] == r2["depot"]["raan_deg"]

    def test_guardrail3_far_future_date_rejected(self):
        """A launch_date more than 14 days beyond TLE epoch must raise 422."""
        from fastapi import HTTPException
        # Compute a date safely beyond 14 days from epoch.
        epoch_dt = main_module._debris_epoch()
        far_date = (epoch_dt + timedelta(days=20)).strftime("%Y-%m-%d")
        req = _make_plan_req(launch_date=far_date)
        with pytest.raises(HTTPException) as exc_info:
            main_module._run_plan(req)
        assert exc_info.value.status_code == 422
        assert "14" in str(exc_info.value.detail)

    def test_guardrail3_date_within_window_accepted(self):
        """A launch_date within 14 days of TLE epoch must be accepted."""
        epoch_dt = main_module._debris_epoch()
        near_date = (epoch_dt + timedelta(days=3)).strftime("%Y-%m-%d")
        req = _make_plan_req(launch_date=near_date)
        # Should not raise.
        result = main_module._run_plan(req)
        assert "depot" in result

    def test_guardrail3_exactly_14_days_accepted(self):
        """Exactly 14-day offset must be accepted (boundary condition)."""
        epoch_dt = main_module._debris_epoch()
        boundary_date = (epoch_dt + timedelta(days=14)).strftime("%Y-%m-%d")
        req = _make_plan_req(launch_date=boundary_date)
        # Should not raise.
        result = main_module._run_plan(req)
        assert "depot" in result

    def test_launch_date_applies_raan_drift(self):
        """With a future launch_date, depot raan_deg must differ from start_raan_deg."""
        epoch_dt = main_module._debris_epoch()
        future_date = (epoch_dt + timedelta(days=7)).strftime("%Y-%m-%d")
        req = _make_plan_req(start_raan_deg=0.0, launch_date=future_date)
        result = main_module._run_plan(req)
        # RAAN drift for 7 days at 800km/74° is non-zero → depot raan != 0.0
        assert result["depot"]["raan_deg"] != 0.0

    def test_invalid_date_format_rejected(self):
        from fastapi import HTTPException
        req = _make_plan_req(launch_date="not-a-date")
        with pytest.raises(HTTPException) as exc_info:
            main_module._run_plan(req)
        assert exc_info.value.status_code == 422

    def test_guardrail2_epoch_anchor_consistent_with_sweep(self):
        """_run_plan and the sweep must use the same epoch anchor (_debris_epoch())."""
        # This verifies that _debris_epoch() is accessible from both code paths.
        # We simply check it returns a UTC datetime and doesn't raise.
        epoch = main_module._debris_epoch()
        assert epoch.tzinfo is not None
        assert isinstance(epoch, datetime)


# ---------------------------------------------------------------------------
# Q1: Frozen epoch under concurrency
# ---------------------------------------------------------------------------

class TestFrozenEpochUnderConcurrency:
    """Within one /sweep-launch-window request, every date-offset result must
    use the SAME _debris_epoch() value even if the underlying mtime changes
    mid-request.

    The bug this guards against: if _debris_epoch() were called independently
    inside each worker thread (rather than once in the handler and closed over),
    a mid-sweep cache refresh would silently anchor different chart points to
    different epochs, corrupting the comparison between them with no visible
    symptom.

    Test strategy: patch os.path.getmtime (the underlying call inside
    _debris_epoch) to return a different value on every successive call.
    Then run a sweep and confirm every launch_date in the window is anchored
    to the SAME base date — the one computed on the first call at handler entry
    — not a mix of dates reflecting multiple different mtime values.
    """

    def test_all_window_launch_dates_anchored_to_same_epoch(self, monkeypatch):
        _sweep_narration_cache.clear()
        monkeypatch.setattr("app.main._groq_client", lambda: _fake_groq_client())

        # _debris_epoch() calls os.path.getmtime.  We make it return a
        # different monotonically-increasing value on every call so that any
        # independent per-thread invocation produces a visibly different result.
        from datetime import datetime, timezone
        import itertools

        base_ts = datetime(2025, 7, 15, 0, 0, 0, tzinfo=timezone.utc).timestamp()
        # Each successive call advances by 1 day worth of seconds.
        call_counter = itertools.count()

        def shifting_getmtime(path):
            n = next(call_counter)
            return base_ts + n * 86400.0  # day 0, day 1, day 2, …

        # Patch at the source so even calls from worker threads hit it.
        monkeypatch.setattr("app.main.os.path.getmtime", shifting_getmtime)
        monkeypatch.setattr("app.main._run_plan", lambda req, **kw: _stub_run_plan_result())

        req = _make_sweep_req(window_days=4)
        result = sweep_launch_window(req)

        # If epoch were re-called per worker, launch_dates would span multiple
        # different base dates.  If frozen correctly, all dates share the SAME
        # base (2025-07-15 + day_offset), so the earliest date in the window
        # must equal the frozen epoch date, and all others are offsets from it.
        valid_entries = [r for r in result["window"] if "error" not in r]
        assert len(valid_entries) > 0, "No valid window entries to check"

        # Extract the base date from the first entry (day_offset 0.0).
        day0_entry = next((r for r in valid_entries if r["day_offset"] == 0.0), None)
        assert day0_entry is not None, "No day_offset=0.0 entry in window"
        base_date_str = day0_entry["launch_date"]  # e.g. "2025-07-15"

        # Every other entry's launch_date must be anchored to the same base.
        # If epoch were re-called per worker, some entries would be offset from
        # day 1, day 2, etc., making their dates jump by more than their day_offset.
        from datetime import date, timedelta
        base_date = date.fromisoformat(base_date_str[:10])

        for entry in valid_entries:
            offset = entry["day_offset"]
            expected_date = base_date + timedelta(days=offset)
            actual_date_str = entry["launch_date"][:10]
            actual_date = date.fromisoformat(actual_date_str)
            assert actual_date == expected_date, (
                f"day_offset={offset}: expected launch_date anchored to {expected_date}, "
                f"got {actual_date}. Epoch was re-called per worker (frozen epoch broken)."
            )

    def test_debris_epoch_called_once_not_per_worker(self, monkeypatch):
        """Count _debris_epoch() calls during a sweep: must be exactly 1."""
        _sweep_narration_cache.clear()
        monkeypatch.setattr("app.main._groq_client", lambda: _fake_groq_client())
        monkeypatch.setattr("app.main._run_plan", lambda req, **kw: _stub_run_plan_result())

        call_count = [0]
        real_debris_epoch = main_module._debris_epoch

        def counting_debris_epoch():
            call_count[0] += 1
            return real_debris_epoch()

        monkeypatch.setattr("app.main._debris_epoch", counting_debris_epoch)

        req = _make_sweep_req(window_days=4)
        sweep_launch_window(req)

        assert call_count[0] == 1, (
            f"_debris_epoch() was called {call_count[0]} times during sweep "
            f"(expected exactly 1 — once at handler entry, then frozen)."
        )


# ---------------------------------------------------------------------------
# Q2: No double-drift
# ---------------------------------------------------------------------------

class TestNoDoubleDrift:
    """The sweep applies RAAN drift exactly once via shifted_raan passed to the
    depot node.  The internal PlanRequest built per-date must NOT carry
    launch_date — doing so would trigger _run_plan's own guardrail logic and
    apply the drift a second time, corrupting every result in the sweep.

    Test strategy:
      1. For a known day_offset, compute the expected single-drift RAAN and
         the expected double-drift RAAN.
      2. Capture the actual start_raan_deg that reaches optimize_route().
      3. Assert it matches the single-drift value and does NOT match the
         double-drift value.
    """

    def test_no_launch_date_on_internal_plan_request(self, monkeypatch):
        """The PlanRequest constructed inside _solve_one_date must have
        launch_date=None so _run_plan's guardrail branch is never entered."""
        _sweep_narration_cache.clear()
        monkeypatch.setattr("app.main._groq_client", lambda: _fake_groq_client())

        captured_reqs = []

        def capturing_run_plan(req, **kw):
            captured_reqs.append(req)
            return _stub_run_plan_result()

        monkeypatch.setattr("app.main._run_plan", capturing_run_plan)

        req = _make_sweep_req(window_days=2)
        sweep_launch_window(req)

        assert len(captured_reqs) > 0, "No _run_plan calls captured"
        for captured in captured_reqs:
            assert captured.launch_date is None, (
                f"Internal PlanRequest has launch_date={captured.launch_date!r} — "
                "this would double-apply RAAN drift: once via shifted_raan, "
                "once via _run_plan's launch_date guardrail."
            )

    def test_raan_at_depot_matches_single_drift_not_double(self, monkeypatch):
        """The RAAN that actually reaches the depot node must equal
        start_raan_deg + single_drift(day_offset), not
        start_raan_deg + double_drift(2 * day_offset)."""
        _sweep_narration_cache.clear()
        monkeypatch.setattr("app.main._groq_client", lambda: _fake_groq_client())

        from app.delta_v import raan_drift_deg

        start_raan = 45.0
        alt = 800.0
        incl = 74.0
        # Use window_days=1 so we have a day_offset=1.0 point to check.
        day_offset_to_check = 1.0
        single_drift = raan_drift_deg(alt, incl, day_offset_to_check)
        expected_raan = start_raan + single_drift

        # Double-drift would be start_raan + raan_drift(2 * day_offset)
        # (since _run_plan would compute drift again from the same anchor).
        # We verify the actual RAAN is NOT near this value.
        double_drift = raan_drift_deg(alt, incl, 2 * day_offset_to_check)
        double_drift_raan = start_raan + double_drift

        captured_raans = {}

        def capturing_run_plan(req, **kw):
            captured_raans[req.start_raan_deg] = req.start_raan_deg
            return _stub_run_plan_result()

        monkeypatch.setattr("app.main._run_plan", capturing_run_plan)

        req = SweepLaunchWindowRequest(
            start_altitude_km=alt,
            start_inclination_deg=incl,
            start_raan_deg=start_raan,
            fuel_budget_km_s=2.5,
            window_days=1,
        )
        sweep_launch_window(req)

        # Find the RAAN used for day_offset=1.0.
        assert len(captured_raans) > 0
        # There should be a call with raan near expected_raan.
        actual_raans = list(captured_raans.keys())
        closest = min(actual_raans, key=lambda r: abs(r - expected_raan))

        assert abs(closest - expected_raan) < 0.001, (
            f"RAAN at depot for day_offset=1.0 was {closest:.6f}, "
            f"expected single-drift value {expected_raan:.6f}. "
            f"Double-drift would give {double_drift_raan:.6f}."
        )
        # Also assert it is NOT the double-drift value (catches the specific bug).
        assert abs(closest - double_drift_raan) > 0.001, (
            f"RAAN matches double-drift value {double_drift_raan:.6f} — "
            "drift is being applied twice!"
        )
