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
import json
import os
import tempfile
import time
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

# Celestrak only refreshes GP data server-side every ~2 hours, and blocks IPs
# that poll more often than that. Cache to a local file so repeated dev/test
# runs (each a fresh process) reuse the same fetch instead of hammering them.
CACHE_FILE = os.path.join(tempfile.gettempdir(), "orbital_clean_debris_cache.json")
CACHE_MAX_AGE_SECONDS = 2 * 60 * 60  # 2 hours, matches Celestrak's own update cadence


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
            subpoint = wgs84.geographic_position_of(geocentric)
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


def get_debris_field(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Main entry point: fetch all groups, merge, filter, cap count.
    Reuses a local cache file if it's younger than CACHE_MAX_AGE_SECONDS,
    since Celestrak's own data doesn't change that often anyway."""
    if not force_refresh and os.path.exists(CACHE_FILE):
        age_seconds = time.time() - os.path.getmtime(CACHE_FILE)
        if age_seconds < CACHE_MAX_AGE_SECONDS:
            with open(CACHE_FILE, "r") as f:
                cached: list[dict[str, Any]] = json.load(f)
            print(f"[cache] Using cached debris field ({age_seconds / 60:.0f} min old, {len(cached)} objects)")
            return cached

    ts = load.timescale()
    all_objects: list[dict[str, Any]] = []

    for group in DEBRIS_GROUPS:
        raw = fetch_group_tles(group)
        all_objects.extend(raw)

    filtered = parse_and_filter(all_objects, ts)
    result = filtered[:MAX_OBJECTS]

    with open(CACHE_FILE, "w") as f:
        json.dump(result, f)
    print(f"[cache] Fetched fresh from Celestrak, cached {len(result)} objects")

    return result


if __name__ == "__main__":
    # Quick manual test — run this directly to sanity-check the pipeline.
    # NOTE: needs real internet access to celestrak.org.
    debris = get_debris_field()
    print(f"Fetched {len(debris)} debris objects in {ALT_MIN_KM}-{ALT_MAX_KM}km band")
    for d in debris[:5]:
        print(d)