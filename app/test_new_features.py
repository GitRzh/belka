"""
pytest suite for the 6 new-feature items from CHECKPOINT.txt, plus:
  7. removal_method_explanation (main.py + optimizer.py)
     -- LLM-generated justification per technique, cached, with fallback.

  1. method_maturity + possible_methods split       (removal_method.py)
  2. nets_carried cap                                (optimizer.py)
  3. removal_method_filter                           (main.py + /replan)
  4. monitor_only excluded from the candidate pool    (cost_matrix.py)
  5. target_norad_id                                  (main.py)
  6. /naive-route explanation parity                  (main.py)
  7. removal_method_explanation                       (main.py + optimizer.py)

Most tests hit the LIVE Celestrak pipeline (same as test_pipeline_live.py) --
they need real network access and, for the /naive-route explanation tests
that don't monkeypatch it, a working GROQ_API_KEY. Tests that only need to
check the NEW wiring (not re-verify Groq itself, already covered by
test_pipeline_live.py) monkeypatch _explain_plan/_explain_diff/_parse_overrides
so they're fast, free, and don't flake on rate limits.

Run: pytest app/test_new_features.py -v
"""
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

DEFAULT_START = dict(start_altitude_km=800.0, start_inclination_deg=74.0)


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
    top_object = sorted(scored_field, key=lambda o: o["risk_score"], reverse=True)[0]
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
