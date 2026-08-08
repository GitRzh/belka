"""
Tests for the launch-site feature.

Covers:
  - derive_start_orbit() unit behaviour (floor enforcement, unknown site,
    default altitude, RAAN from longitude)
  - PlanRequest model_validator: launch_site path and raw-orbit path
  - Regression: existing raw start_position path unchanged
  - /replan free-text parsing: site-only change
  - /replan free-text parsing: weights-only change (site stays, orbit unchanged)
  - /replan free-text parsing: site + weights together
  - /replan free-text parsing: unlisted site in free text (must be ignored)
  - /replan free-text parsing: no site mention (start_position untouched)
  - /plan + weight-only /replan persistence: launch_site on initial /plan,
    weight-only /replan must preserve the resolved orbit unchanged

All tests that touch _parse_overrides or the /replan endpoint are fully
mocked — no network calls, no GROQ_API_KEY required.

All tests that touch _run_plan are also mocked via monkeypatch so no
Celestrak fetch is needed.
"""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.launch_sites import LAUNCH_SITES, derive_start_orbit
from app.main import (
    PlanRequest,
    ReplanRequest,
    _build_parse_prompt,
    _parse_overrides,
)
from app.cost_matrix import DEFAULT_POOL_SIZE


# ---------------------------------------------------------------------------
# Helpers shared across test classes
# ---------------------------------------------------------------------------

def _make_req(**kwargs) -> PlanRequest:
    """Minimal PlanRequest using raw orbit fields."""
    defaults = dict(
        start_altitude_km=800.0,
        start_inclination_deg=74.0,
        fuel_budget_km_s=3.5,
        pool_size=DEFAULT_POOL_SIZE,
        weights=None,
    )
    defaults.update(kwargs)
    return PlanRequest(**defaults)


def _make_replan_req(**kwargs) -> ReplanRequest:
    """Minimal ReplanRequest using raw orbit fields."""
    defaults = dict(
        start_altitude_km=800.0,
        start_inclination_deg=74.0,
        fuel_budget_km_s=3.5,
        pool_size=DEFAULT_POOL_SIZE,
        weights=None,
        user_request_text="placeholder",
    )
    defaults.update(kwargs)
    return ReplanRequest(**defaults)


def _fake_groq_response(content: str):
    msg = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


def _mock_groq_client(*contents: str) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        _fake_groq_response(c) for c in contents
    ]
    return client


# ---------------------------------------------------------------------------
# 1. derive_start_orbit() unit tests
# ---------------------------------------------------------------------------

class TestDeriveStartOrbit(unittest.TestCase):

    def test_unknown_site_raises_clearly(self):
        """Unknown site_key raises ValueError with the key name in the message."""
        with self.assertRaises(ValueError) as ctx:
            derive_start_orbit("atlantis_pad_39a")
        self.assertIn("atlantis_pad_39a", str(ctx.exception))
        self.assertIn("Valid keys", str(ctx.exception))

    def test_min_inclination_floor_enforced(self):
        """Requesting inclination below the site's latitude is clamped to the floor."""
        # Cape Canaveral min_inclination = 28.5 deg
        result = derive_start_orbit("cape_canaveral", inclination=10.0)
        self.assertEqual(result["inclination_deg"], 28.5,
                         "inclination must be clamped to min_inclination floor")

    def test_inclination_above_floor_used_as_given(self):
        """Requesting inclination above the floor uses the requested value."""
        result = derive_start_orbit("cape_canaveral", inclination=51.6)
        self.assertAlmostEqual(result["inclination_deg"], 51.6, places=5)

    def test_default_inclination_is_min_inclination(self):
        """With no inclination argument the result equals site min_inclination."""
        for key, site in LAUNCH_SITES.items():
            result = derive_start_orbit(key)
            self.assertAlmostEqual(
                result["inclination_deg"], site["min_inclination"], places=5,
                msg=f"default inclination wrong for {key}",
            )

    def test_default_altitude_is_800(self):
        """Default altitude_km is 800."""
        result = derive_start_orbit("kourou")
        self.assertEqual(result["altitude_km"], 800.0)

    def test_custom_altitude_passes_through(self):
        """Custom altitude_km is reflected in the result."""
        result = derive_start_orbit("vandenberg", altitude_km=600)
        self.assertEqual(result["altitude_km"], 600.0)

    def test_raan_from_longitude_normalised(self):
        """RAAN = site longitude % 360, always in [0, 360)."""
        # Cape Canaveral lon = -80.6 → raan = 360 - 80.6 = 279.4
        result = derive_start_orbit("cape_canaveral")
        self.assertAlmostEqual(result["raan_deg"], (-80.6) % 360, places=5)
        self.assertGreaterEqual(result["raan_deg"], 0.0)
        self.assertLess(result["raan_deg"], 360.0)

    def test_return_shape_matches_planrequest_start_fields(self):
        """Return dict has exactly altitude_km, inclination_deg, raan_deg."""
        result = derive_start_orbit("baikonur")
        self.assertSetEqual(
            set(result.keys()),
            {"altitude_km", "inclination_deg", "raan_deg"},
        )
        for v in result.values():
            self.assertIsInstance(v, float)

    def test_all_five_sites_are_present(self):
        """All five expected site keys exist in LAUNCH_SITES."""
        expected = {"cape_canaveral", "vandenberg", "kourou", "baikonur", "sriharikota"}
        self.assertSetEqual(set(LAUNCH_SITES.keys()), expected)

    def test_min_inclination_equals_abs_latitude(self):
        """min_inclination == abs(lat) for every site (physical constraint)."""
        for key, site in LAUNCH_SITES.items():
            self.assertAlmostEqual(
                site["min_inclination"], abs(site["lat"]), places=6,
                msg=f"min_inclination != abs(lat) for {key}",
            )


# ---------------------------------------------------------------------------
# 2. PlanRequest model_validator — construction-time behaviour
# ---------------------------------------------------------------------------

class TestPlanRequestValidator(unittest.TestCase):

    def test_raw_orbit_path_unchanged(self):
        """Existing raw start_position fields pass through with no changes."""
        req = PlanRequest(
            start_altitude_km=750.0,
            start_inclination_deg=51.6,
            start_raan_deg=42.0,
            fuel_budget_km_s=2.0,
        )
        self.assertEqual(req.start_altitude_km, 750.0)
        self.assertEqual(req.start_inclination_deg, 51.6)
        self.assertEqual(req.start_raan_deg, 42.0)
        self.assertIsNone(req.launch_site)

    def test_launch_site_populates_raw_fields(self):
        """launch_site without raw fields resolves altitude/incl/raan."""
        req = PlanRequest(
            launch_site="kourou",
            fuel_budget_km_s=2.0,
        )
        self.assertEqual(req.start_altitude_km, 800.0)
        self.assertAlmostEqual(req.start_inclination_deg, 5.2, places=5)
        self.assertAlmostEqual(req.start_raan_deg, (-52.8) % 360, places=4)
        self.assertEqual(req.launch_site, "kourou")

    def test_launch_site_inclination_override(self):
        """launch_site + inclination_deg uses the override (above floor)."""
        req = PlanRequest(
            launch_site="cape_canaveral",
            inclination_deg=51.6,
            fuel_budget_km_s=2.0,
        )
        self.assertAlmostEqual(req.start_inclination_deg, 51.6, places=5)

    def test_launch_site_inclination_override_below_floor_clamped(self):
        """launch_site + inclination_deg below floor is clamped to floor."""
        req = PlanRequest(
            launch_site="cape_canaveral",
            inclination_deg=5.0,   # below cape's 28.5 floor
            fuel_budget_km_s=2.0,
        )
        self.assertAlmostEqual(req.start_inclination_deg, 28.5, places=5)

    def test_neither_raw_nor_launch_site_raises(self):
        """Omitting both launch_site and start_altitude/inclination is a 422."""
        with self.assertRaises(ValidationError) as ctx:
            PlanRequest(fuel_budget_km_s=2.0)
        self.assertIn("launch_site", str(ctx.exception).lower() + "start_altitude" )

    def test_invalid_launch_site_key_raises_in_derive(self):
        """An unrecognised launch_site key raises during validation."""
        with self.assertRaises((ValidationError, ValueError)):
            PlanRequest(launch_site="kennedy_space_center", fuel_budget_km_s=2.0)

    def test_model_dump_roundtrip_is_idempotent(self):
        """PlanRequest(**req.model_dump()) does not re-resolve (idempotency)."""
        req = PlanRequest(launch_site="baikonur", fuel_budget_km_s=2.0)
        dumped = req.model_dump()
        req2 = PlanRequest(**dumped)
        self.assertEqual(req.start_altitude_km,    req2.start_altitude_km)
        self.assertEqual(req.start_inclination_deg, req2.start_inclination_deg)
        self.assertEqual(req.start_raan_deg,        req2.start_raan_deg)
        self.assertEqual(req.launch_site,           req2.launch_site)


# ---------------------------------------------------------------------------
# 3. /replan free-text parsing tests (all Groq calls mocked)
# ---------------------------------------------------------------------------

class TestReplanParseOverridesLaunchSite(unittest.TestCase):

    def _req_with_site(self, site="kourou"):
        return _make_req(
            start_altitude_km=None,
            start_inclination_deg=None,
            launch_site=site,
        )

    def test_site_only_change_parsed(self):
        """Free text naming a listed site → launch_site key, no weight keys."""
        mock_client = _mock_groq_client('{"launch_site": "vandenberg"}')
        req = _make_req()
        with patch("app.main._groq_client", return_value=mock_client):
            result = _parse_overrides("switch to Vandenberg", req)
        self.assertEqual(result.get("launch_site"), "vandenberg")
        self.assertNotIn("weights", result)
        self.assertNotIn("fuel_budget_km_s", result)

    def test_weights_only_change_no_site_key(self):
        """Free text changing only weights → no launch_site key emitted."""
        mock_client = _mock_groq_client('{"weights": {"proximity": 0.8, "lifetime": 0.2}}')
        req = _make_req()
        with patch("app.main._groq_client", return_value=mock_client):
            result = _parse_overrides("prioritise proximity", req)
        self.assertNotIn("launch_site", result)
        self.assertIn("weights", result)

    def test_site_and_weights_parsed_together(self):
        """Free text changing site AND weights → both keys present."""
        payload = '{"launch_site": "baikonur", "weights": {"proximity": 0.6, "lifetime": 0.4}}'
        mock_client = _mock_groq_client(payload)
        req = _make_req()
        with patch("app.main._groq_client", return_value=mock_client):
            result = _parse_overrides("launch from Baikonur and prioritise proximity", req)
        self.assertEqual(result.get("launch_site"), "baikonur")
        self.assertIn("weights", result)

    def test_unlisted_site_in_free_text_omitted(self):
        """LLM correctly omits launch_site for a real but unlisted location."""
        # The LLM is instructed not to invent keys for unlisted sites.
        # We simulate it following the rule by returning no launch_site key.
        mock_client = _mock_groq_client('{"no_changes": true}')
        req = _make_req()
        with patch("app.main._groq_client", return_value=mock_client):
            result = _parse_overrides("launch from Plesetsk instead", req)
        self.assertNotIn("launch_site", result)

    def test_llm_hallucinated_site_key_is_silently_dropped_not_422(self):
        """If the LLM emits an unknown launch_site key despite prompt instructions,
        Step 3 must silently ignore it — not raise 422 — so the request proceeds
        with the existing orbit unchanged.  This tests the defensive fallback for
        a parser malfunction, not normal user input."""
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)

        # /replan needs a valid PlanRequest base.  Use raw orbit so we don't
        # depend on Celestrak — mock _run_plan to return a minimal result.
        minimal_plan = {
            "route": [],
            "route_details": [],
            "visited_count": 0,
            "total_fuel_cost_km_s": 0.0,
            "fuel_budget_km_s": 3.5,
            "fuel_used_fraction": 0.0,
            "total_risk_collected": 0.0,
            "skipped_count": 0,
            "pool_size_used": 0,
            "depot": {
                "altitude_km": 800.0,
                "inclination_deg": 74.0,
                "raan_deg": 0.0,
                "latitude": 0.0,
                "longitude": 0.0,
            },
        }

        # LLM returns a hallucinated site key not in LAUNCH_SITES.
        # _parse_overrides passes it through its allowlist (launch_site is
        # allowed), so it reaches Step 3.  Step 3 must drop it silently.
        hallucinated_response = '{"launch_site": "plesetsk_cosmodrome"}'

        with patch("app.main._groq_client",
                   return_value=_mock_groq_client(hallucinated_response, "{}")):
            with patch("app.main._run_plan", return_value=minimal_plan):
                with patch("app.main._explain_diff", return_value="No change."):
                    resp = client.post("/replan", json={
                        "start_altitude_km": 800.0,
                        "start_inclination_deg": 74.0,
                        "fuel_budget_km_s": 3.5,
                        "user_request_text": "launch from Plesetsk instead",
                    })

        # Must be 200, not 422 — hallucinated key is silently ignored.
        self.assertEqual(resp.status_code, 200,
                         f"expected 200, got {resp.status_code}: {resp.text}")
        body = resp.json()
        # overrides_applied must NOT contain launch_site.
        self.assertNotIn("launch_site", body.get("overrides_applied", {}),
                         "hallucinated launch_site key must not appear in overrides_applied")

    def test_no_site_mention_leaves_start_position_untouched(self):
        """Free text with no site mention → launch_site absent from overrides."""
        mock_client = _mock_groq_client('{"fuel_budget_km_s": 2.0}')
        req = _make_req()
        with patch("app.main._groq_client", return_value=mock_client):
            result = _parse_overrides("cut the fuel in half", req)
        self.assertNotIn("launch_site", result)
        self.assertIn("fuel_budget_km_s", result)

    def test_launch_site_key_in_allowlist(self):
        """launch_site and inclination_deg are in _ALLOWED_OVERRIDE_KEYS."""
        from app.main import _ALLOWED_OVERRIDE_KEYS
        self.assertIn("launch_site",    _ALLOWED_OVERRIDE_KEYS)
        self.assertIn("inclination_deg", _ALLOWED_OVERRIDE_KEYS)

    def test_parse_prompt_includes_site_keys(self):
        """_build_parse_prompt embeds the five site keys so the LLM knows them."""
        req = _make_req()
        prompt = _build_parse_prompt(req)
        for key in LAUNCH_SITES:
            self.assertIn(key, prompt, f"site key {key!r} missing from prompt")

    def test_parse_prompt_includes_no_guess_instruction(self):
        """_build_parse_prompt tells the LLM not to guess unlisted sites."""
        req = _make_req()
        prompt = _build_parse_prompt(req)
        self.assertIn("NOT in the list", prompt,
                      "prompt must instruct LLM not to guess unlisted sites")


# ---------------------------------------------------------------------------
# 4. /replan orbit persistence: launch_site on /plan → weight-only /replan
#    (start_position must be unchanged in the new_req)
# ---------------------------------------------------------------------------

class TestReplanOrbitPersistence(unittest.TestCase):

    def _fake_plan_result(self):
        return {
            "route": [],
            "route_details": [],
            "visited_count": 0,
            "total_fuel_cost_km_s": 0.0,
            "fuel_budget_km_s": 3.5,
            "fuel_used_fraction": 0.0,
            "total_risk_collected": 0.0,
            "skipped_count": 0,
            "pool_size_used": 0,
            "depot": {
                "altitude_km": 800.0,
                "inclination_deg": 5.2,
                "raan_deg": 307.2,
                "latitude": 0.0,
                "longitude": 0.0,
            },
        }

    def test_weight_only_replan_preserves_orbit_from_launch_site(self):
        """After a launch_site /plan, a weight-only /replan must keep the same
        start_altitude_km, start_inclination_deg, start_raan_deg on new_req."""
        # Build the initial ReplanRequest as the /replan endpoint receives it.
        # The frontend sends launch_site (no raw fields); the model_validator
        # resolves them; they end up on the req object.
        initial_req = ReplanRequest(
            launch_site="kourou",
            fuel_budget_km_s=3.5,
            user_request_text="increase proximity weight",
        )
        # Confirm the validator resolved the orbit from the site.
        self.assertAlmostEqual(initial_req.start_inclination_deg, 5.2, places=5)

        # Simulate _parse_overrides returning a weights-only override.
        weight_override = {"weights": {"proximity": 0.8, "lifetime": 0.2}}

        # Replicate the Step 4 merge logic from /replan exactly.
        new_req_data = initial_req.model_dump()
        new_req_data.update(weight_override)
        # No "launch_site" in weight_override → raw fields NOT nulled out.
        new_req_data.pop("user_request_text", None)
        new_req = PlanRequest(**new_req_data)

        # The orbit must be identical to the original resolution.
        self.assertEqual(new_req.start_altitude_km,    initial_req.start_altitude_km)
        self.assertEqual(new_req.start_inclination_deg, initial_req.start_inclination_deg)
        self.assertEqual(new_req.start_raan_deg,        initial_req.start_raan_deg)
        self.assertEqual(new_req.launch_site,           "kourou")

    def test_site_change_replan_resolves_new_orbit(self):
        """A /replan with launch_site in overrides must re-resolve the orbit."""
        initial_req = ReplanRequest(
            launch_site="kourou",
            fuel_budget_km_s=3.5,
            user_request_text="switch to Vandenberg",
        )
        site_override = {"launch_site": "vandenberg"}

        new_req_data = initial_req.model_dump()
        new_req_data.update(site_override)
        # This is the key Step 4 guard: null out raw fields on site change.
        new_req_data["start_altitude_km"]    = None
        new_req_data["start_inclination_deg"] = None
        new_req_data.pop("user_request_text", None)
        new_req = PlanRequest(**new_req_data)

        # Vandenberg: min_inclination=34.6, lon=-120.6 → raan=239.4
        self.assertEqual(new_req.launch_site, "vandenberg")
        self.assertAlmostEqual(new_req.start_inclination_deg, 34.6, places=5)
        self.assertAlmostEqual(new_req.start_raan_deg, (-120.6) % 360, places=4)
        # Old orbit is gone.
        self.assertNotAlmostEqual(new_req.start_inclination_deg, 5.2, places=1)


# ---------------------------------------------------------------------------
# 5. /launch-sites endpoint
# ---------------------------------------------------------------------------

class TestLaunchSitesEndpoint(unittest.TestCase):

    def test_endpoint_returns_all_five_sites(self):
        """GET /launch-sites returns all five site keys."""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        resp = client.get("/launch-sites")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertSetEqual(
            set(body.keys()),
            {"cape_canaveral", "vandenberg", "kourou", "baikonur", "sriharikota"},
        )

    def test_endpoint_response_has_required_fields(self):
        """Each site entry has name, lat, lon, min_inclination."""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        resp = client.get("/launch-sites")
        body = resp.json()
        for key, entry in body.items():
            for field in ("name", "lat", "lon", "min_inclination"):
                self.assertIn(field, entry, f"{key} missing field {field!r}")


# ---------------------------------------------------------------------------
# 6. /naive-route launch_site support
# ---------------------------------------------------------------------------

class TestNaiveRouteLaunchSite(unittest.TestCase):
    """Tests for the launch_site capability added to /naive-route."""

    def setUp(self):
        from fastapi.testclient import TestClient
        from app.main import app
        self.client = TestClient(app)
        # Patch _get_scored_field so no Celestrak fetch is needed.
        self._scored_patcher = patch(
            "app.main._get_scored_field",
            return_value=[],
        )
        self._scored_patcher.start()

    def tearDown(self):
        self._scored_patcher.stop()

    def test_site_only_call_succeeds(self):
        """GET /naive-route?launch_site=kourou&fuel_budget_km_s=3.5 → 200."""
        resp = self.client.get(
            "/naive-route",
            params={"launch_site": "kourou", "fuel_budget_km_s": 3.5},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_neither_site_nor_raw_fields_gives_422(self):
        """Omitting both launch_site and start_altitude/inclination → 422."""
        resp = self.client.get(
            "/naive-route",
            params={"fuel_budget_km_s": 3.5},
        )
        self.assertEqual(resp.status_code, 422)
        detail = resp.json().get("detail", "")
        self.assertIn("launch_site", detail)
        self.assertIn("start_altitude_km", detail)

    def test_unknown_site_key_gives_422_not_silent_drop(self):
        """An unknown launch_site value must raise 422 listing valid keys."""
        resp = self.client.get(
            "/naive-route",
            params={"launch_site": "plesetsk_cosmodrome", "fuel_budget_km_s": 3.5},
        )
        self.assertEqual(resp.status_code, 422)
        detail = resp.json().get("detail", "")
        self.assertIn("plesetsk_cosmodrome", detail)
        self.assertIn("Valid keys", detail)

    def test_raw_fields_only_path_unchanged(self):
        """Regression: raw start_altitude_km + start_inclination_deg still works."""
        resp = self.client.get(
            "/naive-route",
            params={
                "start_altitude_km": 750.0,
                "start_inclination_deg": 51.6,
                "fuel_budget_km_s": 3.5,
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        # Depot must reflect the raw values.
        depot = body.get("depot", {})
        self.assertAlmostEqual(depot["altitude_km"], 750.0, places=3)
        self.assertAlmostEqual(depot["inclination_deg"], 51.6, places=3)

    def test_site_path_depot_matches_plan_resolution(self):
        """Parity: /naive-route?launch_site=X depot == /plan resolution for X.

        derive_start_orbit is the shared codepath; we call it directly and
        compare against what naive_route puts in the response depot — no LLM
        or optimizer involved.
        """
        orbit = derive_start_orbit("cape_canaveral")
        resp = self.client.get(
            "/naive-route",
            params={"launch_site": "cape_canaveral", "fuel_budget_km_s": 3.5},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        depot = resp.json().get("depot", {})
        self.assertAlmostEqual(depot["altitude_km"],    orbit["altitude_km"],    places=4)
        self.assertAlmostEqual(depot["inclination_deg"], orbit["inclination_deg"], places=4)
        self.assertAlmostEqual(depot["raan_deg"],        orbit["raan_deg"],        places=4)


if __name__ == "__main__":
    unittest.main()
