"""
Launch-site catalog and orbit derivation.

Provides a fixed dict of five real launch sites and a helper that converts
a site key + optional inclination into the same circular-orbit shape that
PlanRequest uses for start_altitude_km / start_inclination_deg / start_raan_deg.

RAAN derivation
---------------
RAAN (right ascension of the ascending node) for a freshly-launched
spacecraft is approximated as the launch-site longitude normalised to
[0, 360).  This matches the convention used throughout the codebase for
debris objects: tle_fetch.py reads sat.model.nodeo (radians) and converts
to degrees; the result is a plain float treated as an inertial angle in
delta_v.py.  For a due-east launch the ascending node crosses the prime
meridian at the same local longitude as the launch site, making
`raan_deg = longitude % 360` the standard first-order approximation.

Inclination floor
-----------------
Physics constrains the minimum achievable inclination to the site's
latitude (a due-east launch from latitude φ produces inclination φ).
Higher inclinations are always reachable via a yaw dogleg; lower ones
are not physically possible without a costly plane change after orbit
insertion.  min_inclination is therefore set equal to abs(lat) and is
enforced as a hard floor in derive_start_orbit().
"""
from typing import Optional

LAUNCH_SITES: dict[str, dict] = {
    "cape_canaveral": {
        "name": "Cape Canaveral",
        "lat": 28.5,
        "lon": -80.6,
        "min_inclination": 28.5,
    },
    "vandenberg": {
        "name": "Vandenberg",
        "lat": 34.6,
        "lon": -120.6,
        "min_inclination": 34.6,
    },
    "kourou": {
        "name": "Kourou",
        "lat": 5.2,
        "lon": -52.8,
        "min_inclination": 5.2,
    },
    "baikonur": {
        "name": "Baikonur",
        "lat": 45.9,
        "lon": 63.3,
        "min_inclination": 45.9,
    },
    "sriharikota": {
        "name": "Sriharikota",
        "lat": 13.7,
        "lon": 80.2,
        "min_inclination": 13.7,
    },
}


def derive_start_orbit(
    site_key: str,
    inclination: Optional[float] = None,
    altitude_km: float = 800,
) -> dict[str, float]:
    """Derive a circular starting orbit from a launch site.

    Parameters
    ----------
    site_key:
        One of the keys in LAUNCH_SITES.
    inclination:
        Desired orbital inclination in degrees.  If provided, the result is
        max(inclination, site['min_inclination']) — the physical floor is
        always enforced.  If omitted, defaults to site['min_inclination']
        (due-east launch).
    altitude_km:
        Target circular orbit altitude in km.  Defaults to 800 km (the
        centre of the debris band this planner targets).

    Returns
    -------
    dict with keys altitude_km, inclination_deg, raan_deg — the same shape
    as the three start_* fields on PlanRequest so it can be unpacked
    directly into a PlanRequest construction dict.

    Raises
    ------
    ValueError if site_key is not in LAUNCH_SITES.
    """
    if site_key not in LAUNCH_SITES:
        raise ValueError(
            f"Unknown launch site {site_key!r}. "
            f"Valid keys: {sorted(LAUNCH_SITES)}"
        )

    site = LAUNCH_SITES[site_key]
    floor = site["min_inclination"]

    if inclination is None:
        resolved_inclination = floor
    else:
        resolved_inclination = max(float(inclination), floor)

    # RAAN approximation: longitude of the launch site normalised to [0, 360).
    # Mirrors tle_fetch.py's sat.model.nodeo * 57.29577951308232 convention —
    # both produce a plain degrees float that delta_v.py treats as an inertial
    # angle.  For a freshly-launched spacecraft the ascending node longitude
    # equals the sub-satellite-point longitude at the moment of launch, which
    # is the site longitude to first order.
    raan_deg = site["lon"] % 360

    return {
        "altitude_km": float(altitude_km),
        "inclination_deg": round(resolved_inclination, 6),
        "raan_deg": round(raan_deg, 6),
    }
