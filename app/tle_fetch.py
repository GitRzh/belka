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
from datetime import datetime, timezone
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
CELESTRAK_SATCAT_BASE = "https://celestrak.org/satcat/records.php"
DEBRIS_GROUPS = ["cosmos-2251-debris", "iridium-33-debris", "fengyun-1c-debris"]

ALT_MIN_KM = 700
ALT_MAX_KM = 1000
MAX_OBJECTS = 300  # total cap for hackathon performance, split evenly per group below

# BUG FOUND VIA LIVE TESTING (confirmed against real Celestrak data): capping
# the *combined* list to MAX_OBJECTS after concatenating all groups means
# whichever group is fetched first eats the whole cap if it's big enough on
# its own. Real-world check: cosmos-2251-debris alone has 300+ real fragments
# in the 700-1000km band, so with DEBRIS_GROUPS fetched in listed order,
# iridium-33-debris and fengyun-1c-debris NEVER made it into the result at
# all -- confirmed via `Counter(o['name'].split()[0] for o in cache)` coming
# back 100% COSMOS. Fix: filter and cap each group independently, then
# combine, so every debris cloud gets guaranteed shelf space regardless of
# fetch order or relative size.
PER_GROUP_MAX_OBJECTS = MAX_OBJECTS // len(DEBRIS_GROUPS)

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


def fetch_group_satcat(group: str) -> dict[int, float | None]:
    """Fetch SATCAT records for one debris group and return a mapping of
    NORAD_CAT_ID -> RCS (m², float) or None for objects where RCS is
    null/missing/empty.  Uses the same GROUP parameter accepted by the GP
    endpoint so no separate ID list is needed."""
    url = f"{CELESTRAK_SATCAT_BASE}?GROUP={group}&FORMAT=JSON"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    records: list[dict[str, Any]] = resp.json()
    result: dict[int, float | None] = {}
    for rec in records:
        norad_id = rec.get("NORAD_CAT_ID")
        if norad_id is None:
            continue
        rcs_raw = rec.get("RCS")
        # RCS may be absent, null (JSON null → None), an empty string, or a
        # valid float.  Treat anything that isn't a real positive number as None
        # rather than 0 — a zero RCS is physically meaningless for debris and
        # would silently distort min-max normalisation in risk_score.py.
        rcs: float | None = None
        if rcs_raw is not None and rcs_raw != "":
            try:
                parsed = float(rcs_raw)
                rcs = parsed if parsed > 0.0 else None
            except (ValueError, TypeError):
                rcs = None
        result[int(norad_id)] = rcs
    return result


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
                    # RAAN (right ascension of ascending node) -- was previously
                    # parsed by skyfield/sgp4 (sat.model.nodeo) but discarded.
                    # Needed so delta_v.py can compute the true relative angle
                    # between two orbital planes instead of assuming two
                    # objects with the same inclination sit in the same plane.
                    # Same source, same units-conversion pattern as inclo above.
                    "raan_deg": round(_f(sat.model.nodeo) * 57.29577951308232, 4),
                    "latitude": round(_f(subpoint.latitude.degrees), 4),
                    "longitude": round(_f(subpoint.longitude.degrees), 4),
                    "bstar": float(obj.get("BSTAR", 0.0) or 0.0),
                    # TLEs go stale fast -- accuracy degrades the further t is
                    # from the epoch. t - sat.epoch is skyfield's own day-count
                    # subtraction (Time.__sub__ returns a plain float number of
                    # days), so this needs no _f() wrapper. Surfacing this lets
                    # a reviewer see how fresh the orbital data actually is,
                    # rather than trusting position/risk numbers as ground truth.
                    "epoch_age_days": round(t - sat.epoch, 2),
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
    result: list[dict[str, Any]] = []

    for group in DEBRIS_GROUPS:
        raw = fetch_group_tles(group)
        filtered_group = parse_and_filter(raw, ts)
        capped_group = filtered_group[:PER_GROUP_MAX_OBJECTS]
        result.extend(capped_group)
        print(f"[fetch] {group}: {len(filtered_group)} in band, kept {len(capped_group)} (cap={PER_GROUP_MAX_OBJECTS})")

    # Join SATCAT RCS data.  Fetch all groups into a single norad_id -> rcs_m2
    # map, then annotate every object before writing the cache so subsequent
    # cache hits include RCS without a second network round-trip.
    rcs_map: dict[int, float | None] = {}
    for group in DEBRIS_GROUPS:
        try:
            rcs_map.update(fetch_group_satcat(group))
        except Exception as exc:
            # SATCAT is optional enrichment — don't abort the whole fetch if it
            # fails (e.g. rate-limit, schema change).  Objects will get None.
            print(f"[satcat] Warning: could not fetch SATCAT for {group}: {exc}")

    for obj in result:
        obj["rcs_m2"] = rcs_map.get(obj["norad_id"])  # None if not found

    rcs_count = sum(1 for o in result if o["rcs_m2"] is not None)
    print(f"[satcat] RCS coverage: {rcs_count}/{len(result)} objects have non-null rcs_m2")

    with open(CACHE_FILE, "w") as f:
        json.dump(result, f)
    print(f"[cache] Fetched fresh from Celestrak, cached {len(result)} objects")

    return result


def get_cache_timestamp() -> str:
    """Returns ISO 8601 UTC timestamp of when CACHE_FILE was last written.
    Assumes get_debris_field() has been called at least once already
    (CACHE_FILE exists)."""
    mtime = os.path.getmtime(CACHE_FILE)
    return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()


if __name__ == "__main__":
    # Quick manual test — run this directly to sanity-check the pipeline.
    # NOTE: needs real internet access to celestrak.org.
    debris = get_debris_field()
    print(f"Fetched {len(debris)} debris objects in {ALT_MIN_KM}-{ALT_MAX_KM}km band")
    for d in debris[:5]:
        print(d)