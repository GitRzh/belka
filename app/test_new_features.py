"""
pytest suite for the 7 new-feature items from CHECKPOINT.txt:
  1. method_maturity + possible_methods split       (removal_method.py)
  2. nets_carried cap                                (optimizer.py)
  3. removal_method_filter                           (main.py + /replan)
  4. monitor_only excluded from the candidate pool    (cost_matrix.py)
  5. target_norad_id                                  (main.py)
  6. /naive-route explanation parity                  (main.py)
  7. removal_method_explanation                       (main.py + optimizer.py)
     -- LLM-generated justification per technique, cached, with fallback.

Most tests hit the LIVE Celestrak pipeline (same as test_pipeline_live.py) --
they need real network access and, for the /naive-route explanation tests
that don't monkeypatch it, a working GROQ_API_KEY. Tests that only need to
check the NEW wiring (not re-verify Groq itself, already covered by
test_pipeline_live.py) monkeypatch _explain_plan/_explain_diff/_parse_overrides
so they're fast, free, and don't flake on rate limits.

Run: pytest app/test_new_features.py -v
"""
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

import groq as groq_module
import app.main as main_module
from app.main import PlanRequest, ReplanRequest, _run_plan, naive_route, replan, _get_scored_field
from app.cost_matrix import select_candidate_pool
from app.removal_method import (
    add_removal_methods,
    METHOD_ROBOTIC_ARM_OR_NET,
    METHOD_NET_CAPTURE,
    METHOD_MONITOR_ONLY,
    TECHNIQUE_ROBOTIC_ARM,
    TECHNIQUE_NET_CAPTURE,
    MATURITY_CONCEPTUAL,
    MATURITY_FLIGHT_DEMONSTRATED,
    MATURITY_OPERATIONAL,
)

DEFAULT_START: dict[str, Any] = dict(start_altitude_km=800.0, start_inclination_deg=74.0)


@pytest.fixture(scope="module")
def scored_field():
    """One live Celestrak fetch, reused across every test in this module."""
    field = _get_scored_field()
    assert field, "Debris field empty -- check Celestrak reachability before blaming these tests"
    return field


# --------------------------------------------------------------------------- #
# Item 1 -- method_maturity + possible_methods split (removal_method.py)
# --------------------------------------------------------------------------- #

def test_intact_gets_two_possible_methods(scored_field):
    intact = [o for o in scored_field if o["object_type"] == "intact"]
    assert intact, "No intact objects in current field -- can't exercise this case"
    for o in intact:
        assert o["removal_method"] == METHOD_ROBOTIC_ARM_OR_NET
        assert set(o["possible_methods"]) == {TECHNIQUE_ROBOTIC_ARM, TECHNIQUE_NET_CAPTURE}
        assert o["method_maturity"][TECHNIQUE_ROBOTIC_ARM] == MATURITY_CONCEPTUAL
        assert o["method_maturity"][TECHNIQUE_NET_CAPTURE] == MATURITY_FLIGHT_DEMONSTRATED


def test_net_capture_fragment_has_single_method(scored_field):
    net_objs = [o for o in scored_field if o["removal_method"] == METHOD_NET_CAPTURE]
    assert net_objs, "No net_capture fragments in current field"
    for o in net_objs:
        assert o["possible_methods"] == [TECHNIQUE_NET_CAPTURE]
        assert o["method_maturity"] == {TECHNIQUE_NET_CAPTURE: MATURITY_FLIGHT_DEMONSTRATED}


def test_monitor_only_fragment_has_single_method(scored_field):
    mon_objs = [o for o in scored_field if o["removal_method"] == METHOD_MONITOR_ONLY]
    assert mon_objs, "No monitor_only fragments in current field"
    for o in mon_objs:
        assert o["possible_methods"] == ["monitor_only"]
        assert o["method_maturity"] == {"monitor_only": MATURITY_OPERATIONAL}


def test_removal_method_still_bare_string_backward_compat(scored_field):
    for o in scored_field:
        assert isinstance(o["removal_method"], str)
        assert o["removal_method"] in {METHOD_ROBOTIC_ARM_OR_NET, METHOD_NET_CAPTURE, METHOD_MONITOR_ONLY}


def test_add_removal_methods_empty_input_is_empty_output():
    assert add_removal_methods([]) == []


# --------------------------------------------------------------------------- #
# Item 4 -- monitor_only excluded from the candidate pool (cost_matrix.py)
# --------------------------------------------------------------------------- #

def test_monitor_only_never_in_pool(scored_field):
    pool = select_candidate_pool(scored_field, pool_size=len(scored_field))
    assert all(o["removal_method"] != METHOD_MONITOR_ONLY for o in pool)


def test_pool_size_shrinks_by_exactly_monitor_only_count(scored_field):
    monitor_count = sum(1 for o in scored_field if o["removal_method"] == METHOD_MONITOR_ONLY)
    routable_count = len(scored_field) - monitor_count
    pool = select_candidate_pool(scored_field, pool_size=len(scored_field))
    assert len(pool) == routable_count


def test_select_candidate_pool_backward_compat_no_removal_method_key():
    """Objects with no removal_method field (e.g. callers that skip
    add_removal_methods) must pass through unfiltered -- .get() default,
    never a KeyError, never wrongly excluded."""
    synthetic = [{"norad_id": i, "name": f"X{i}", "risk_score": 1.0 - i * 0.1} for i in range(5)]
    pool = select_candidate_pool(synthetic, pool_size=5)
    assert len(pool) == 5


# --------------------------------------------------------------------------- #
# Item 2 -- nets_carried cap (optimizer.py, via /plan's _run_plan)
# --------------------------------------------------------------------------- #

def test_nets_carried_defaults_to_one(scored_field):
    req = PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=100)
    result = _run_plan(req)
    net_visits = sum(1 for d in result["route_details"] if d["removal_method"] == METHOD_NET_CAPTURE)
    assert net_visits <= 1
    assert result["net_capacity_constrained"] == 1


def test_nets_carried_raised_allows_more(scored_field):
    req = PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=100, nets_carried=5)
    result = _run_plan(req)
    net_visits = sum(1 for d in result["route_details"] if d["removal_method"] == METHOD_NET_CAPTURE)
    assert net_visits <= 5
    assert result["net_capacity_constrained"] == 5


def test_nets_carried_monotonic_with_cap(scored_field):
    """Raising the cap should never visit FEWER net_capture stops."""
    counts = []
    for cap in (1, 3, 10):
        req = PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=100, nets_carried=cap)
        result = _run_plan(req)
        counts.append(sum(1 for d in result["route_details"] if d["removal_method"] == METHOD_NET_CAPTURE))
    assert counts[0] <= counts[1] <= counts[2]


def test_nets_carried_holds_across_budgets(scored_field):
    for budget in (0.5, 2.5, 10.0):
        req = PlanRequest(**DEFAULT_START, fuel_budget_km_s=budget, pool_size=100, nets_carried=2)
        result = _run_plan(req)
        net_visits = sum(1 for d in result["route_details"] if d["removal_method"] == METHOD_NET_CAPTURE)
        assert net_visits <= 2, f"nets_carried=2 violated at fuel_budget_km_s={budget}"


# --------------------------------------------------------------------------- #
# Item 1 downstream -- route_details carries possible_methods/method_maturity
# --------------------------------------------------------------------------- #

def test_route_details_has_possible_methods_and_maturity(scored_field):
    req = PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=50)
    result = _run_plan(req)
    assert result["visited_count"] > 0, "Need at least one visited node to check this"
    for d in result["route_details"]:
        assert "possible_methods" in d and isinstance(d["possible_methods"], list)
        assert "method_maturity" in d and isinstance(d["method_maturity"], dict)


# --------------------------------------------------------------------------- #
# Item 3 -- removal_method_filter (main.py PlanRequest + /replan override)
# --------------------------------------------------------------------------- #

def test_filter_net_capture_only(scored_field):
    req = PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=50,
                       removal_method_filter=METHOD_NET_CAPTURE, nets_carried=50)
    result = _run_plan(req)
    assert result["visited_count"] > 0
    assert all(d["removal_method"] == METHOD_NET_CAPTURE for d in result["route_details"])


def test_filter_robotic_arm_or_net_capture_only(scored_field):
    req = PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=50,
                       removal_method_filter=METHOD_ROBOTIC_ARM_OR_NET)
    result = _run_plan(req)
    assert all(d["removal_method"] == METHOD_ROBOTIC_ARM_OR_NET for d in result["route_details"])


def test_filter_monitor_only_rejected(scored_field):
    req = PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, removal_method_filter=METHOD_MONITOR_ONLY)
    with pytest.raises(HTTPException) as exc:
        _run_plan(req)
    assert exc.value.status_code == 422


def test_filter_garbage_value_rejected(scored_field):
    req = PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, removal_method_filter="laser_broom")
    with pytest.raises(HTTPException) as exc:
        _run_plan(req)
    assert exc.value.status_code == 422


def test_filter_unset_never_includes_monitor_only(scored_field):
    """Baseline (no filter) still respects item 4 -- monitor_only excluded regardless."""
    req = PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=50)
    result = _run_plan(req)
    assert all(d["removal_method"] != METHOD_MONITOR_ONLY for d in result["route_details"])


def test_replan_sets_removal_method_filter(monkeypatch, scored_field):
    monkeypatch.setattr("app.main._parse_overrides",
                         lambda user_text, req: {"removal_method_filter": METHOD_NET_CAPTURE})
    monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff explanation")
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")

    req = ReplanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=50, nets_carried=50,
                         user_request_text="switch to net capture only")
    result = replan(req)
    assert result["overrides_applied"] == {"removal_method_filter": METHOD_NET_CAPTURE}
    assert all(d["removal_method"] == METHOD_NET_CAPTURE for d in result["new_plan"]["route_details"])


def test_replan_clears_removal_method_filter_with_explicit_null(monkeypatch, scored_field):
    monkeypatch.setattr("app.main._parse_overrides",
                         lambda user_text, req: {"removal_method_filter": None})
    monkeypatch.setattr("app.main._explain_diff", lambda diff: "stub diff explanation")
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")

    req = ReplanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=50,
                         removal_method_filter=METHOD_NET_CAPTURE,
                         user_request_text="use any method now")
    result = replan(req)
    assert result["overrides_applied"] == {"removal_method_filter": None}
    assert result["new_plan"]["route_details"]  # sanity: still got a route back


def test_replan_rejects_invalid_filter_value_from_llm(monkeypatch, scored_field):
    monkeypatch.setattr("app.main._parse_overrides",
                         lambda user_text, req: {"removal_method_filter": "monitor_only"})
    req = ReplanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, user_request_text="only track them")
    with pytest.raises(HTTPException) as exc:
        replan(req)
    assert exc.value.status_code == 422


# --------------------------------------------------------------------------- #
# Item 5 -- target_norad_id (main.py PlanRequest)
# --------------------------------------------------------------------------- #

def test_target_norad_id_forces_low_risk_object_into_pool(scored_field):
    net_objs = sorted([o for o in scored_field if o["removal_method"] == METHOD_NET_CAPTURE],
                       key=lambda o: o["risk_score"])
    assert net_objs, "Need at least one net_capture object for this test"
    low_risk_target = net_objs[0]["norad_id"]

    baseline = _run_plan(PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=1))
    forced = _run_plan(PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=1,
                                    target_norad_id=low_risk_target))
    assert forced["pool_size_used"] == baseline["pool_size_used"] + 1


def test_target_norad_id_already_in_pool_no_duplicate(scored_field):
    # Top risk_score alone isn't enough -- a monitor_only object can outrank
    # everything else (it happened live: IRIDIUM 33 DEB, norad_id 46734) but
    # is never a valid route target. This test is about pool dedup, not
    # about removal_method, so restrict to routable objects first.
    routable = [o for o in scored_field if o["removal_method"] != METHOD_MONITOR_ONLY]
    assert routable, "Need at least one non-monitor_only object for this test"
    top_object = sorted(routable, key=lambda o: o["risk_score"], reverse=True)[0]
    baseline = _run_plan(PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=50))
    with_target = _run_plan(PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=50,
                                         target_norad_id=top_object["norad_id"]))
    assert with_target["pool_size_used"] == baseline["pool_size_used"]


def test_target_norad_id_monitor_only_rejected(scored_field):
    monitor_objs = [o for o in scored_field if o["removal_method"] == METHOD_MONITOR_ONLY]
    assert monitor_objs, "Need at least one monitor_only object for this test"
    req = PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, target_norad_id=monitor_objs[0]["norad_id"])
    with pytest.raises(HTTPException) as exc:
        _run_plan(req)
    assert exc.value.status_code == 422


def test_target_norad_id_not_found(scored_field):
    # Guaranteed absent regardless of which live objects are currently in the field.
    impossible_id = max(o["norad_id"] for o in scored_field) + 10_000_000
    req = PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, target_norad_id=impossible_id)
    with pytest.raises(HTTPException) as exc:
        _run_plan(req)
    assert exc.value.status_code == 404


def test_target_norad_id_excluded_by_filter_still_404s_with_hint(scored_field):
    """A real object, but one the active removal_method_filter has already
    stripped out of `scored` -- should still 404, with the filter mentioned
    in the message so it's not a confusing dead end."""
    intact_objs = [o for o in scored_field if o["removal_method"] == METHOD_ROBOTIC_ARM_OR_NET]
    assert intact_objs, "Need at least one intact object for this test"
    req = PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0,
                       removal_method_filter=METHOD_NET_CAPTURE,
                       target_norad_id=intact_objs[0]["norad_id"])
    with pytest.raises(HTTPException) as exc:
        _run_plan(req)
    assert exc.value.status_code == 404
    assert "removal_method_filter" in exc.value.detail


# --------------------------------------------------------------------------- #
# Item 6 -- /naive-route explanation parity (main.py)
# --------------------------------------------------------------------------- #

def test_naive_route_has_route_details(monkeypatch):
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")
    result = naive_route(**DEFAULT_START, fuel_budget_km_s=5.0)
    assert "route_details" in result
    if result["route_details"]:
        assert "removal_method" in result["route_details"][0]
        assert "possible_methods" in result["route_details"][0]
        assert "method_maturity" in result["route_details"][0]


def test_naive_route_never_visits_monitor_only(monkeypatch):
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")
    result = naive_route(**DEFAULT_START, fuel_budget_km_s=10.0)
    assert all(d["removal_method"] != METHOD_MONITOR_ONLY for d in result["route_details"])


def test_naive_route_explanation_soft_fails_cleanly(monkeypatch):
    """Whether Groq succeeds or fails, /naive-route must never hard-fail --
    either a string explanation, or None + explanation_error, never neither."""
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: None)
    result = naive_route(**DEFAULT_START, fuel_budget_km_s=5.0)
    if result["visited_count"] > 0:
        assert result["explanation"] is None
        assert "explanation_error" in result


def test_naive_route_explanation_present_on_success(monkeypatch):
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing text")
    result = naive_route(**DEFAULT_START, fuel_budget_km_s=5.0)
    if result["visited_count"] > 0:
        assert result["explanation"] == "stub briefing text"
        assert "explanation_error" not in result


# --------------------------------------------------------------------------- #
# Item 7 -- removal_method_explanation (main.py + optimizer.py)
# --------------------------------------------------------------------------- #

def test_explanation_cache_limits_llm_calls(monkeypatch):
    """Calling _get_scored_field() on a live batch (hundreds of objects, all
    sharing one of 3 removal_method values) must result in at most 3 distinct
    LLM calls -- one per unique removal_method value -- regardless of batch
    size.  The cache must absorb all repeated calls for the same method."""
    call_log: list[str] = []

    def fake_groq_call(model, messages, temperature):
        # Extract the removal_method from the prompt to log which key was hit.
        content = messages[0]["content"]
        for line in content.splitlines():
            if line.startswith("Removal method label:"):
                call_log.append(line.split(":", 1)[1].strip())
                break

        # Return a minimal object shaped like a real Groq response.
        class FakeChoice:
            class FakeMessage:
                content = "Stub explanation from monkeypatched LLM."
            message = FakeMessage()

        class FakeResp:
            choices = [FakeChoice()]

        return FakeResp()

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    return fake_groq_call(**kwargs)

    # Clear the module-level cache so this test starts clean regardless of
    # run order (other tests / the scored_field fixture may have warmed it).
    main_module._REMOVAL_METHOD_EXPLANATION_CACHE.clear()
    monkeypatch.setattr("app.main._groq_client", lambda: FakeClient())

    field = _get_scored_field()
    assert field, "Debris field must not be empty for this test"

    distinct_methods = {o["removal_method"] for o in field}
    # Every object must have both new fields.
    for o in field:
        assert "removal_method_explanation" in o, f"Missing explanation on {o['norad_id']}"
        assert "removal_method_explanation_source" in o
        assert o["removal_method_explanation"], "Explanation must be non-empty"

    # The LLM must have been called at most once per distinct removal_method --
    # not once per object (which would be hundreds of redundant calls).
    assert len(call_log) <= len(distinct_methods), (
        f"Expected at most {len(distinct_methods)} LLM calls (one per distinct method), "
        f"got {len(call_log)}: {call_log}"
    )
    # And it must have been called for each distinct method exactly once
    # (cache miss on first encounter, hit on all subsequent objects).
    assert set(call_log) == distinct_methods, (
        f"Expected calls for exactly {distinct_methods}, got calls for {set(call_log)}"
    )


def test_route_details_carries_removal_method_explanation(monkeypatch):
    """route_details entries from _run_plan() must carry removal_method_explanation.
    This proves the optimizer.py wiring (explicit field in the dict comprehension),
    not just the main.py enrichment."""
    main_module._REMOVAL_METHOD_EXPLANATION_CACHE.clear()

    class FakeClient:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    class FakeChoice:
                        class FakeMessage:
                            content = "Stub explanation."
                        message = FakeMessage()
                    class FakeResp:
                        choices = [FakeChoice()]
                    return FakeResp()

    monkeypatch.setattr("app.main._groq_client", lambda: FakeClient())
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")

    req = PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=50, nets_carried=5)
    result = _run_plan(req)
    assert result["visited_count"] > 0, "Need at least one visited node to check optimizer wiring"
    for d in result["route_details"]:
        assert "removal_method_explanation" in d, (
            f"route_details entry missing removal_method_explanation: {d}"
        )
        assert isinstance(d["removal_method_explanation"], str)
        # Field must be non-empty: either the stub LLM text or the fallback template.
        assert d["removal_method_explanation"], (
            f"removal_method_explanation is empty string for {d['removal_method']}"
        )


def test_explanation_fallback_on_groq_failure(monkeypatch):
    """When Groq raises APIConnectionError, _explain_removal_method must
    not raise -- it must return a non-empty fallback string with
    removal_method_explanation_source == 'fallback', and cache that fallback
    so the dead API is not re-hit for subsequent objects with the same method."""
    main_module._REMOVAL_METHOD_EXPLANATION_CACHE.clear()

    fake_request = httpx.Request("POST", "https://api.groq.com/v1/chat/completions")

    def raise_connection_error(**kwargs):
        raise groq_module.APIConnectionError(
            message="simulated connection failure",
            request=fake_request,
        )

    class FailingClient:
        class chat:
            class completions:
                create = staticmethod(raise_connection_error)

    monkeypatch.setattr("app.main._groq_client", lambda: FailingClient())

    from app.main import _explain_removal_method
    from app.removal_method import METHOD_NET_CAPTURE, METHOD_ROBOTIC_ARM_OR_NET

    explanation, source = _explain_removal_method(
        METHOD_NET_CAPTURE,
        ["net_capture"],
        {"net_capture": "flight_demonstrated"},
    )
    assert explanation, "Fallback explanation must be non-empty"
    assert source == "fallback"

    # Verify the fallback was cached -- a second call must not hit the client again
    # (if it did, raise_connection_error would fire a second time, but since the
    # cache now holds the result, _groq_client is never called again).
    explanation2, source2 = _explain_removal_method(
        METHOD_NET_CAPTURE,
        ["net_capture"],
        {"net_capture": "flight_demonstrated"},
    )
    assert explanation2 == explanation
    assert source2 == "fallback"

    # Also verify _get_scored_field doesn't surface the error to the caller --
    # all objects must still get a non-empty explanation field even under failure.
    field = _get_scored_field()
    for o in field:
        assert o.get("removal_method_explanation"), (
            f"removal_method_explanation empty under failure for norad_id={o['norad_id']}"
        )
        assert o["removal_method_explanation_source"] == "fallback"


# --------------------------------------------------------------------------- #
# Item 8 -- data_quality label + max_tle_age_days filter
# --------------------------------------------------------------------------- #

_VALID_DATA_QUALITY_VALUES = {"fresh", "aging", "stale"}


def test_every_object_has_data_quality_label(scored_field):
    """data_quality must be present on every object from _get_scored_field(),
    unconditionally, with a valid label value.  This is the 'always visible,
    never filtered' transparency guarantee."""
    for o in scored_field:
        assert "data_quality" in o, f"data_quality missing on norad_id={o['norad_id']}"
        assert o["data_quality"] in _VALID_DATA_QUALITY_VALUES, (
            f"Unexpected data_quality value {o['data_quality']!r} on norad_id={o['norad_id']}"
        )


def test_data_quality_label_matches_epoch_age_days(scored_field):
    """data_quality must be consistent with epoch_age_days per the documented
    thresholds: < 7 = 'fresh', 7-14 = 'aging', > 14 = 'stale'."""
    from app.main import _data_quality
    for o in scored_field:
        expected = _data_quality(o["epoch_age_days"])
        assert o["data_quality"] == expected, (
            f"data_quality mismatch: got {o['data_quality']!r}, "
            f"expected {expected!r} for epoch_age_days={o['epoch_age_days']}"
        )


def test_stale_object_excluded_from_plan_at_default_threshold(monkeypatch):
    """An object with epoch_age_days > 14 must be excluded from _run_plan()'s
    candidate pool at the default max_tle_age_days=14, and included when
    max_tle_age_days is raised past its age.

    Strategy: monkeypatch _get_scored_field() to inject a synthetic stale
    object (epoch_age_days=20.0) with a very high risk_score so it would be
    first into the pool if not filtered, then confirm it's absent at default
    threshold and present when the threshold is raised."""
    import app.main as main_module
    from app.main import _data_quality

    # Build a synthetic stale object shaped like a real scored+enriched object.
    stale_norad_id = 999_999_901  # guaranteed not in any real Celestrak field
    stale_epoch_age = 20.0
    stale_obj = {
        "norad_id": stale_norad_id,
        "name": "SYNTHETIC STALE DEB",
        "altitude_km": 800.0,
        "inclination_deg": 74.0,
        "raan_deg": 0.0,
        "latitude": 0.0,
        "longitude": 0.0,
        "bstar": 0.00001,
        "epoch_age_days": stale_epoch_age,
        "risk_score": 1.0,          # highest possible so it ranks into pool first
        "proximity_score": 1.0,
        "lifetime_score": 1.0,
        "size_score": None,
        "size_score_available": False,
        "rcs_m2": None,
        "object_type": "fragment",
        "removal_method": "net_capture",
        "possible_methods": ["net_capture"],
        "method_maturity": {"net_capture": "flight_demonstrated"},
        "removal_method_explanation": "stub",
        "removal_method_explanation_source": "fallback",
        "data_quality": _data_quality(stale_epoch_age),
    }

    real_field = _get_scored_field()
    patched_field = [stale_obj] + real_field  # stale object first = highest risk rank

    monkeypatch.setattr("app.main._get_scored_field", lambda **kw: patched_field)
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")

    # At default threshold (14.0): stale object must not appear in the pool.
    req_default = PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=50,
                               nets_carried=5)
    assert req_default.max_tle_age_days == 14.0, "Default must be 14.0"
    result_default = _run_plan(req_default)
    visited_ids_default = {d["norad_id"] for d in result_default["route_details"]}
    assert stale_norad_id not in visited_ids_default, (
        f"Stale object (epoch_age_days={stale_epoch_age}) appeared in route "
        f"at default max_tle_age_days=14.0 -- filter not applied"
    )

    # With threshold raised above the object's age: it must now be eligible
    # (not guaranteed visited since the optimizer still decides, but it must
    # be in the pool -- verify via pool_size_used change or presence in the
    # skipped_names if not visited).
    req_raised = PlanRequest(**DEFAULT_START, fuel_budget_km_s=10.0, pool_size=50,
                              nets_carried=5, max_tle_age_days=25.0)
    result_raised = _run_plan(req_raised)
    all_considered_ids = (
        {d["norad_id"] for d in result_raised["route_details"]}
        | {
            # extract norad_id from skipped_names labels like "NAME (norad_id)"
            int(m.group(1))
            for label in result_raised.get("skipped_names", [])
            for m in [__import__("re").search(r"\((\d+)\)$", label)]
            if m
        }
    )
    assert stale_norad_id in all_considered_ids, (
        f"Stale object not in pool when max_tle_age_days=25.0 "
        f"(epoch_age_days={stale_epoch_age}) -- filter too aggressive"
    )


def test_debris_field_endpoint_always_returns_stale_objects(monkeypatch):
    """/debris-field must NOT filter by epoch_age_days -- stale objects must
    always appear with their data_quality label intact, so users can browse
    the full field and decide for themselves.  Prove this by injecting a
    stale object and confirming it survives the listing endpoint unchanged."""
    from app.main import _data_quality

    stale_epoch_age = 30.0
    stale_norad_id = 999_999_902
    stale_obj = {
        "norad_id": stale_norad_id,
        "name": "SYNTHETIC STALE DEB2",
        "altitude_km": 800.0,
        "inclination_deg": 74.0,
        "raan_deg": 0.0,
        "latitude": 0.0,
        "longitude": 0.0,
        "bstar": 0.00001,
        "epoch_age_days": stale_epoch_age,
        "risk_score": 1.0,
        "proximity_score": 1.0,
        "lifetime_score": 1.0,
        "size_score": None,
        "size_score_available": False,
        "rcs_m2": None,
        "object_type": "fragment",
        "removal_method": "net_capture",
        "possible_methods": ["net_capture"],
        "method_maturity": {"net_capture": "flight_demonstrated"},
        "removal_method_explanation": "stub",
        "removal_method_explanation_source": "fallback",
        "data_quality": _data_quality(stale_epoch_age),
    }

    real_field = _get_scored_field()
    patched_field = real_field + [stale_obj]

    monkeypatch.setattr("app.main._get_scored_field", lambda **kw: patched_field)

    # Call the actual debris_field endpoint function directly (not via HTTP).
    from app.main import debris_field as debris_field_endpoint
    response = debris_field_endpoint()

    returned_ids = {o["norad_id"] for o in response["debris_field"]}
    assert stale_norad_id in returned_ids, (
        "Stale object was filtered out of /debris-field -- listing endpoint "
        "must never apply max_tle_age_days filtering"
    )
    stale_in_response = next(o for o in response["debris_field"] if o["norad_id"] == stale_norad_id)
    assert stale_in_response["data_quality"] == "stale"


def test_naive_route_respects_max_tle_age_days(monkeypatch):
    """naive_route() must exclude objects with epoch_age_days > max_tle_age_days,
    the same way _run_plan() does -- so the naive baseline and the AI route
    operate on the same data quality window."""
    from app.main import _data_quality

    stale_epoch_age = 20.0
    stale_norad_id = 999_999_903
    stale_obj = {
        "norad_id": stale_norad_id,
        "name": "SYNTHETIC STALE DEB3",
        "altitude_km": 800.0,
        "inclination_deg": 74.0,
        "raan_deg": 0.0,
        "latitude": 0.0,
        "longitude": 0.0,
        "bstar": 0.00001,
        "epoch_age_days": stale_epoch_age,
        "risk_score": 1.0,
        "proximity_score": 1.0,
        "lifetime_score": 1.0,
        "size_score": None,
        "size_score_available": False,
        "rcs_m2": None,
        "object_type": "fragment",
        "removal_method": "net_capture",
        "possible_methods": ["net_capture"],
        "method_maturity": {"net_capture": "flight_demonstrated"},
        "removal_method_explanation": "stub",
        "removal_method_explanation_source": "fallback",
        "data_quality": _data_quality(stale_epoch_age),
    }

    real_field = _get_scored_field()
    patched_field = [stale_obj] + real_field

    monkeypatch.setattr("app.main._get_scored_field", lambda **kw: patched_field)
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")

    # At default threshold (14.0): stale object absent from naive route too.
    result_default = naive_route(**DEFAULT_START, fuel_budget_km_s=10.0,
                                  pool_size=50, max_tle_age_days=14.0)
    visited_names_default = {d["name"] for d in result_default["route_details"]}
    assert stale_obj["name"] not in visited_names_default, (
        "Stale object appeared in naive_route at max_tle_age_days=14.0 -- filter not applied"
    )

    # With raised threshold: the stale object is now eligible and must appear
    # in the route (visited) or in the skipped count.  We use pool_size=2 so
    # the pool is small enough that the stale object -- first in the patched
    # field -- is guaranteed to rank in, not crowded out by 50 real objects.
    result_raised = naive_route(**DEFAULT_START, fuel_budget_km_s=10.0,
                                 pool_size=2, max_tle_age_days=25.0)
    visited_names_raised = {d["name"] for d in result_raised["route_details"]}
    # The stale object has risk_score=1.0 so it's always in the top-2 pool.
    # It will be visited or (if budget-exhausted) counted in skipped_count.
    # Either way, pool_size_used should be 2 and include the stale object.
    # Simplest verifiable invariant: the pool at raised threshold is larger
    # than at tight threshold (pool_size=2 vs pool_size=2, both under 14 days
    # the live field has enough objects, but with the stale object filtered
    # out there are still >=2 real objects, so the difference is route content).
    # Instead verify directly: pool_size=1 at default drops stale, =1 at
    # raised includes stale (it's highest-risk so it's always slot 0).
    result_tiny_default = naive_route(**DEFAULT_START, fuel_budget_km_s=10.0,
                                       pool_size=1, max_tle_age_days=14.0)
    result_tiny_raised = naive_route(**DEFAULT_START, fuel_budget_km_s=10.0,
                                      pool_size=1, max_tle_age_days=25.0)
    # At pool_size=1 + default threshold: the stale object is filtered out,
    # so the sole pool slot goes to a real object -- not the stale one.
    first_default_name = (result_tiny_default["route_details"][0]["name"]
                          if result_tiny_default["route_details"] else
                          result_tiny_default.get("skipped_names", [None])[0])
    # At pool_size=1 + raised threshold: stale object is risk_score=1.0
    # (highest), so it wins the single pool slot.
    first_raised_name = (result_tiny_raised["route_details"][0]["name"]
                         if result_tiny_raised["route_details"] else
                         result_tiny_raised.get("skipped_names", [None])[0])
    assert first_default_name != stale_obj["name"], (
        "Stale object took pool slot at default threshold -- filter not applied"
    )
    assert first_raised_name == stale_obj["name"], (
        f"Stale object did not take pool slot when max_tle_age_days=25.0 "
        f"(got {first_raised_name!r}); filter is too aggressive or risk ranking broken"
    )


# --------------------------------------------------------------------------- #
# Pre-deploy fixes — /naive-route shape parity (FIX #2 and FIX #3)
# --------------------------------------------------------------------------- #

# Expected step keys in both /plan (via optimize_route) and /naive-route after fix.
_EXPECTED_STEP_KEYS = {"from", "to", "delta_v_km_s", "arrival_time_days", "raan_drift_deg"}


def test_naive_route_step_breakdown_has_all_five_keys(monkeypatch):
    """After FIX #2: every step in naive_route()'s step_breakdown must carry the
    same five keys that optimizer.py produces.  This is a regression guard so
    the asymmetry can't silently recur."""
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")
    result = naive_route(**DEFAULT_START, fuel_budget_km_s=10.0)
    assert result["visited_count"] > 0, (
        "Need at least one visited node; raise fuel_budget_km_s if live data changed"
    )
    for i, step in enumerate(result["step_breakdown"]):
        assert set(step.keys()) == _EXPECTED_STEP_KEYS, (
            f"step_breakdown[{i}] has keys {set(step.keys())!r}, "
            f"expected {_EXPECTED_STEP_KEYS!r}"
        )


def test_naive_route_step_arrival_time_days_increases(monkeypatch):
    """arrival_time_days must be monotonically non-decreasing across steps
    (elapsed time never goes backward) and must be 0.0 on the first leg."""
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")
    result = naive_route(**DEFAULT_START, fuel_budget_km_s=10.0)
    steps = result["step_breakdown"]
    if not steps:
        return  # nothing to check
    assert steps[0]["arrival_time_days"] == 0.0, (
        f"First step arrival_time_days should be 0.0, got {steps[0]['arrival_time_days']}"
    )
    for i in range(1, len(steps)):
        assert steps[i]["arrival_time_days"] >= steps[i - 1]["arrival_time_days"], (
            f"arrival_time_days decreased from step {i-1} to step {i}: "
            f"{steps[i-1]['arrival_time_days']} -> {steps[i]['arrival_time_days']}"
        )


def test_naive_route_step_raan_drift_deg_is_zero(monkeypatch):
    """raan_drift_deg must be 0.0 on every step: naive_route doesn't model drift."""
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")
    result = naive_route(**DEFAULT_START, fuel_budget_km_s=10.0)
    for i, step in enumerate(result["step_breakdown"]):
        assert step["raan_drift_deg"] == 0.0, (
            f"step_breakdown[{i}]['raan_drift_deg'] = {step['raan_drift_deg']!r}, expected 0.0"
        )


def test_naive_route_has_pool_size_used(monkeypatch):
    """After FIX #3: pool_size_used must be present and equal to len(pool)."""
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")
    result = naive_route(**DEFAULT_START, fuel_budget_km_s=10.0)
    assert "pool_size_used" in result, "pool_size_used missing from naive_route result"
    assert isinstance(result["pool_size_used"], int)
    assert result["pool_size_used"] > 0


def test_naive_route_has_skipped_names(monkeypatch):
    """After FIX #3: skipped_names must be present (may be empty list when all visited)."""
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")
    result = naive_route(**DEFAULT_START, fuel_budget_km_s=10.0)
    assert "skipped_names" in result, "skipped_names missing from naive_route result"
    assert isinstance(result["skipped_names"], list)
    assert len(result["skipped_names"]) == result["skipped_count"]


def test_naive_route_has_min_depot_hop_km_s(monkeypatch):
    """After FIX #3: min_depot_hop_km_s must be present and be a realistic km/s value."""
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")
    result = naive_route(**DEFAULT_START, fuel_budget_km_s=10.0)
    assert "min_depot_hop_km_s" in result, "min_depot_hop_km_s missing from naive_route result"
    val = result["min_depot_hop_km_s"]
    assert isinstance(val, float)
    # Sanity range: any plausible LEO hop is between 0.01 km/s and 20 km/s.
    assert 0.01 <= val <= 20.0, (
        f"min_depot_hop_km_s={val} is outside the plausible km/s range for a LEO hop"
    )


def test_naive_route_zero_visit_produces_warning(monkeypatch):
    """After FIX #3: a tiny fuel budget that forces zero visits must produce
    a non-null warning with min_depot_hop_km_s embedded in the message.
    This is the exact gap that was silent before the fix."""
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")
    result = naive_route(**DEFAULT_START, fuel_budget_km_s=0.001)
    if result["visited_count"] > 0:
        # Budget was large enough on the live pool; skip rather than fail --
        # the real-data cheapest hop can shift between Celestrak refreshes.
        return
    assert "warning" in result, (
        "zero visited_count on naive_route produced no warning field (silent failure)"
    )
    assert result["warning"], "warning field is present but empty"
    assert "min_depot_hop_km_s" in result, (
        "min_depot_hop_km_s missing when visited_count==0"
    )
    assert "min_risk_penalty_scale_needed" in result, (
        "min_risk_penalty_scale_needed missing when visited_count==0"
    )
    # Confirm the hop value in the warning message matches the field value.
    hop = result["min_depot_hop_km_s"]
    assert str(hop) in result["warning"], (
        f"min_depot_hop_km_s value {hop} not embedded in warning message"
    )


def test_naive_route_min_depot_hop_matches_cost_matrix(monkeypatch):
    """min_depot_hop_km_s must match the independently computed cheapest
    depot->node cost from build_cost_matrix() — same verification as the
    original test pass that caught this gap."""
    from app.cost_matrix import build_cost_matrix, select_candidate_pool
    from app.main import _get_scored_field

    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")

    # Build the same pool naive_route uses internally.
    scored = _get_scored_field()
    scored_filtered = [o for o in scored if o.get("epoch_age_days", 0.0) <= 14.0]
    pool = select_candidate_pool(scored_filtered, pool_size=40)

    depot = {
        "norad_id": -1, "name": "DEPOT", "altitude_km": 800.0,
        "inclination_deg": 74.0, "raan_deg": 0.0, "risk_score": 0.0,
    }
    nodes = [depot] + pool
    matrix = build_cost_matrix(nodes)
    expected_min_hop = round(min(matrix[0][1:]), 4)

    result = naive_route(**DEFAULT_START, fuel_budget_km_s=0.001)
    assert result["min_depot_hop_km_s"] == expected_min_hop, (
        f"min_depot_hop_km_s mismatch: response={result['min_depot_hop_km_s']}, "
        f"independently computed={expected_min_hop}"
    )


def test_naive_route_labels_include_norad_id(monkeypatch):
    """route and skipped_names must use 'Name (norad_id)' labels, matching
    optimizer.py's _label() format.  DebrisGlobe.jsx's noradIdFromRouteLabel()
    regex requires the trailing (digits) to resolve globe positions; plain
    names produce null IDs and the polyline silently never draws."""
    monkeypatch.setattr("app.main._explain_plan", lambda route_result: "stub briefing")
    result = naive_route(**DEFAULT_START, fuel_budget_km_s=10.0)

    import re
    label_re = re.compile(r".+ \(\d+\)$")

    for label in result["route"]:
        assert label_re.match(label), (
            f"route label {label!r} missing '(norad_id)' suffix — "
            "DebrisGlobe polyline will not draw for this entry"
        )
    for label in result["skipped_names"]:
        assert label_re.match(label), (
            f"skipped_names label {label!r} missing '(norad_id)' suffix"
        )


# --------------------------------------------------------------------------- #
# Phase 2 -- POST /mission-cost (solve_forced_route / Custom Selection)
# --------------------------------------------------------------------------- #

from app.optimizer import solve_forced_route
from app.main import MissionCostRequest, mission_cost


# ---------------------------------------------------------------------------
# Synthetic fixed test set: 3 nodes with known orbital elements so the
# expected fuel cost can be bounded / compared.  All three share the same
# altitude band, keeping Hohmann costs small and RAAN the dominant variable.
# ---------------------------------------------------------------------------

_FIXED_TARGETS: list[dict] = [
    {
        "norad_id": 99001,
        "name": "TEST DEB-A",
        "altitude_km": 800.0,
        "inclination_deg": 74.0,
        "raan_deg": 0.0,
        "risk_score": 0.6,
        "object_type": "fragment",
        "removal_method": "net_capture",
        "possible_methods": ["net_capture"],
        "method_maturity": {"net_capture": "flight_demonstrated"},
        "removal_method_explanation": "",
        "bstar": 0.00005,
    },
    {
        "norad_id": 99002,
        "name": "TEST DEB-B",
        "altitude_km": 810.0,
        "inclination_deg": 74.2,
        "raan_deg": 5.0,
        "risk_score": 0.5,
        "object_type": "fragment",
        "removal_method": "net_capture",
        "possible_methods": ["net_capture"],
        "method_maturity": {"net_capture": "flight_demonstrated"},
        "removal_method_explanation": "",
        "bstar": 0.00005,
    },
    {
        "norad_id": 99003,
        "name": "TEST INTACT-C",
        "altitude_km": 820.0,
        "inclination_deg": 74.5,
        "raan_deg": 10.0,
        "risk_score": 0.8,
        "object_type": "intact",
        "removal_method": "robotic_arm_or_net_capture",
        "possible_methods": ["robotic_arm", "net_capture"],
        "method_maturity": {"robotic_arm": "flight_demonstrated", "net_capture": "flight_demonstrated"},
        "removal_method_explanation": "",
        "bstar": 0.00001,
    },
]

_FIXED_START = dict(start_altitude_km=800.0, start_inclination_deg=74.0, start_raan_deg=0.0)


def test_forced_route_visits_every_target():
    """All three targets must appear in the route exactly once."""
    result = solve_forced_route(_FIXED_TARGETS, **_FIXED_START)
    assert "error" not in result, f"Unexpected solver error: {result.get('error')}"
    assert result["visited_count"] == len(_FIXED_TARGETS), (
        f"Expected {len(_FIXED_TARGETS)} visits, got {result['visited_count']}"
    )
    returned_ids = {d["norad_id"] for d in result["route_details"]}
    expected_ids = {o["norad_id"] for o in _FIXED_TARGETS}
    assert returned_ids == expected_ids, (
        f"route_details IDs {returned_ids} != expected {expected_ids}"
    )


def test_forced_route_no_duplicate_visits():
    """Each target must appear exactly once -- no duplicate nodes."""
    result = solve_forced_route(_FIXED_TARGETS, **_FIXED_START)
    assert "error" not in result
    ids = [d["norad_id"] for d in result["route_details"]]
    assert len(ids) == len(set(ids)), f"Duplicate visit detected: {ids}"


def test_forced_route_nets_carried_required_matches_net_capture_count():
    """nets_carried_required must equal the count of net_capture targets in the
    fixed set -- 2 of the 3 test objects have removal_method='net_capture'."""
    expected_net_count = sum(
        1 for o in _FIXED_TARGETS if o["removal_method"] == "net_capture"
    )
    result = solve_forced_route(_FIXED_TARGETS, **_FIXED_START)
    assert "error" not in result
    assert result["nets_carried_required"] == expected_net_count, (
        f"nets_carried_required={result['nets_carried_required']} but {expected_net_count} "
        "net_capture targets are in the fixed test set"
    )


def test_forced_route_warning_present_when_nets_gt_1():
    """A selection with > 1 net_capture target must carry a 'warning' key."""
    result = solve_forced_route(_FIXED_TARGETS, **_FIXED_START)
    assert "error" not in result
    assert result["nets_carried_required"] > 1, "Pre-condition: fixed set needs > 1 net_capture"
    assert "warning" in result, "Expected warning about multi-net requirement, got none"
    assert "RemoveDEBRIS" in result["warning"]


def test_forced_route_no_warning_when_nets_eq_1():
    """A single net_capture target must not emit a warning."""
    single_net = [_FIXED_TARGETS[0]]  # exactly one net_capture
    result = solve_forced_route(single_net, **_FIXED_START)
    assert "error" not in result
    assert result["nets_carried_required"] == 1
    assert "warning" not in result, f"Unexpected warning: {result.get('warning')}"


def test_forced_route_fuel_cost_is_positive_and_finite():
    """total_fuel_cost_km_s must be > 0 for any multi-node selection."""
    result = solve_forced_route(_FIXED_TARGETS, **_FIXED_START)
    assert "error" not in result
    assert result["total_fuel_cost_km_s"] > 0, "Expected non-zero fuel cost for a 3-node route"
    import math
    assert math.isfinite(result["total_fuel_cost_km_s"]), "Fuel cost is not finite"


def test_forced_route_step_breakdown_length_matches_visited():
    """step_breakdown must have one entry per visited node."""
    result = solve_forced_route(_FIXED_TARGETS, **_FIXED_START)
    assert "error" not in result
    assert len(result["step_breakdown"]) == result["visited_count"], (
        f"step_breakdown length {len(result['step_breakdown'])} != visited_count {result['visited_count']}"
    )


def test_forced_route_route_details_has_removal_method():
    """Every route_details entry must carry a removal_method string."""
    result = solve_forced_route(_FIXED_TARGETS, **_FIXED_START)
    assert "error" not in result
    for detail in result["route_details"]:
        assert "removal_method" in detail
        assert isinstance(detail["removal_method"], str)


def test_forced_route_fuel_cost_plausible_range():
    """For objects in the same 800-820 km band with RAAN within 10 deg,
    total 3-stop fuel cost should be < 5.0 km/s (a loose sanity bound)."""
    result = solve_forced_route(_FIXED_TARGETS, **_FIXED_START)
    assert "error" not in result
    assert result["total_fuel_cost_km_s"] < 5.0, (
        f"Fuel cost {result['total_fuel_cost_km_s']} km/s looks implausibly high for nearby targets"
    )


# --------------------------------------------------------------------------- #
# /mission-cost endpoint tests (via direct function calls, mocking the field)
# --------------------------------------------------------------------------- #

def test_mission_cost_monitor_only_rejected(monkeypatch):
    """A monitor_only ID in target_norad_ids must raise HTTP 422 before the
    solver is ever called -- same gate as /plan's target_norad_id check."""
    monitor_obj = {
        "norad_id": 55555,
        "name": "MONITOR ONLY OBJ",
        "altitude_km": 800.0,
        "inclination_deg": 74.0,
        "raan_deg": 0.0,
        "risk_score": 0.1,
        "removal_method": "monitor_only",
        "bstar": 0.001,
    }
    monkeypatch.setattr(
        "app.main._get_scored_field",
        lambda **kw: [monitor_obj] + _FIXED_TARGETS,
    )
    req = MissionCostRequest(
        **_FIXED_START,
        target_norad_ids=[55555],
    )
    import pytest as _pytest
    with _pytest.raises(Exception) as exc_info:
        mission_cost(req)
    # FastAPI raises HTTPException; check status code and message
    assert exc_info.value.status_code == 422
    assert "monitor_only" in exc_info.value.detail


def test_mission_cost_unknown_id_rejected(monkeypatch):
    """An ID not in the current debris field must raise HTTP 404."""
    monkeypatch.setattr(
        "app.main._get_scored_field",
        lambda **kw: _FIXED_TARGETS,
    )
    req = MissionCostRequest(
        **_FIXED_START,
        target_norad_ids=[99999],  # not in _FIXED_TARGETS
    )
    import pytest as _pytest
    with _pytest.raises(Exception) as exc_info:
        mission_cost(req)
    assert exc_info.value.status_code == 404
    assert "99999" in exc_info.value.detail


def test_mission_cost_returns_expected_shape(monkeypatch):
    """Happy path: all valid IDs -> response has the required keys."""
    monkeypatch.setattr(
        "app.main._get_scored_field",
        lambda **kw: _FIXED_TARGETS,
    )
    req = MissionCostRequest(
        **_FIXED_START,
        target_norad_ids=[o["norad_id"] for o in _FIXED_TARGETS],
    )
    result = mission_cost(req)
    required_keys = {
        "route", "route_details", "visited_count",
        "total_fuel_cost_km_s", "nets_carried_required", "depot",
    }
    missing = required_keys - result.keys()
    assert not missing, f"Response missing keys: {missing}"


def test_mission_cost_all_targets_in_route(monkeypatch):
    """Every requested ID must appear in route_details exactly once."""
    monkeypatch.setattr(
        "app.main._get_scored_field",
        lambda **kw: _FIXED_TARGETS,
    )
    req = MissionCostRequest(
        **_FIXED_START,
        target_norad_ids=[o["norad_id"] for o in _FIXED_TARGETS],
    )
    result = mission_cost(req)
    returned_ids = {d["norad_id"] for d in result["route_details"]}
    expected_ids = {o["norad_id"] for o in _FIXED_TARGETS}
    assert returned_ids == expected_ids


def test_mission_cost_nets_carried_required_correct(monkeypatch):
    """nets_carried_required must match net_capture count in selection."""
    monkeypatch.setattr(
        "app.main._get_scored_field",
        lambda **kw: _FIXED_TARGETS,
    )
    expected_net_count = sum(
        1 for o in _FIXED_TARGETS if o["removal_method"] == "net_capture"
    )
    req = MissionCostRequest(
        **_FIXED_START,
        target_norad_ids=[o["norad_id"] for o in _FIXED_TARGETS],
    )
    result = mission_cost(req)
    assert result["nets_carried_required"] == expected_net_count


def test_mission_cost_depot_echoed(monkeypatch):
    """Response must echo the depot orbit back so the frontend can draw the
    first leg (same contract as /plan)."""
    monkeypatch.setattr(
        "app.main._get_scored_field",
        lambda **kw: _FIXED_TARGETS,
    )
    req = MissionCostRequest(
        **_FIXED_START,
        target_norad_ids=[_FIXED_TARGETS[0]["norad_id"]],
    )
    result = mission_cost(req)
    assert "depot" in result
    assert result["depot"]["altitude_km"] == _FIXED_START["start_altitude_km"]
    assert result["depot"]["inclination_deg"] == _FIXED_START["start_inclination_deg"]
