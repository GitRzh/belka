"""
Module A: TLE ingestion.

Pulls known debris-cloud groups from Celestrak (free, no API key) and filters
to a curated subset in the 700-1000km LEO band — the most congested zone,
per NASA ODPO findings.

Debris groups chosen because they're real, well-tracked collision events:
- cosmos-2251-debris: 2009 Iridium-Cosmos collision (~800-900km band)
- iridium-33-debris: same collision, other satellite
- fengyun-1c-debris: 2007 Chinese ASAT test (~850km band, huge debris count)
"""
import requests
from typing import Any
from skyfield.api import EarthSatellite, load, wgs84


def _f(x: object) -> float:
    """
    Skyfield's Distance.km / Angle.degrees are lazy-computed properties
    built on an untyped internal descriptor ('reify'), so static type
    checkers can't confirm they resolve to plain floats even though they
    always do at runtime. This is a real gap in skyfield's stubs, not
    something fixable by calling it differently -- so we contain the
    single unavoidable type-ignore here instead of repeating it everywhere.
    """
    return float(x)  # pyright: ignore[reportArgumentType]

CELESTRAK_BASE = "https://celestrak.org/NORAD/elements/gp.php"
DEBRIS_GROUPS = ["cosmos-2251-debris", "iridium-33-debris", "fengyun-1c-debris"]

ALT_MIN_KM = 700
ALT_MAX_KM = 1000
MAX_OBJECTS = 300  # cap for hackathon performance


def fetch_group_tles(group: str) -> list[dict[str, Any]]:
    """Fetch one debris group as JSON from Celestrak."""
    url = f"{CELESTRAK_BASE}?GROUP={group}&FORMAT=json"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def parse_and_filter(raw_objects: list[dict[str, Any]], ts) -> list[dict[str, Any]]:
    """Turn raw Celestrak JSON records into EarthSatellites, filter by altitude band."""
    results = []
    for obj in raw_objects:
        try:
            sat = EarthSatellite.from_omm(ts, obj)
            t = ts.now()
            geocentric = sat.at(t)
            subpoint = wgs84.subpoint_of(geocentric)
            alt_km = _f(subpoint.elevation.km)

            if ALT_MIN_KM <= alt_km <= ALT_MAX_KM:
                results.append({
                    "norad_id": int(sat.model.satnum),
                    "name": obj.get("OBJECT_NAME", "UNKNOWN"),
                    "altitude_km": round(alt_km, 2),
                    "inclination_deg": round(_f(sat.model.inclo) * 57.29577951308232, 4),
                    "latitude": round(_f(subpoint.latitude.degrees), 4),
                    "longitude": round(_f(subpoint.longitude.degrees), 4),
                    "bstar": float(obj.get("BSTAR", 0.0) or 0.0),
                })
        except Exception:
            # Skip malformed/decayed objects rather than crash the whole fetch
            continue
    return results


def get_debris_field() -> list[dict[str, Any]]:
    """Main entry point: fetch all groups, merge, filter, cap count."""
    ts = load.timescale()
    all_objects = []

    for group in DEBRIS_GROUPS:
        raw = fetch_group_tles(group)
        all_objects.extend(raw)

    filtered = parse_and_filter(all_objects, ts)
    return filtered[:MAX_OBJECTS]


if __name__ == "__main__":
    # Quick manual test — run this directly to sanity-check the pipeline.
    # NOTE: needs real internet access to celestrak.org.
    debris = get_debris_field()
    print(f"Fetched {len(debris)} debris objects in {ALT_MIN_KM}-{ALT_MAX_KM}km band")
    for d in debris[:5]:
        print(d)
