"""
Module B, step 1: Delta-v (fuel cost) between two orbits.

This is the hardest math in the project and the thing the whole optimizer
stands on, so it gets the same treatment as Module A: don't trust it just
because it runs, prove the numbers against known real transfers (see the
__main__ block).

Two things drive the cost of moving from one debris object's orbit to
another's:

1. Altitude change — closed-form Hohmann transfer delta-v. Cheap, well
   understood, two burns (leave circular orbit 1, arrive circular orbit 2).

2. Inclination change — the expensive one. Changing an orbital plane costs
   delta-v proportional to the velocity you're carrying when you do it, so
   the same plane change is far cheaper done slowly at a high, low-velocity
   orbit than snapped at a low, fast one. This is why real missions (e.g.
   GTO -> GEO insertion) bundle the plane change into the burn at apogee
   instead of doing it separately.

SIMPLIFYING ASSUMPTIONS (stated up front, not hidden):
- Orbits are treated as circular (fine for debris tracking data, which
  gives us altitude/inclination, not full osculating elements).
- "Inclination change" uses |incl1 - incl2| only. A fully rigorous plane
  change also depends on the relative RAAN (right ascension of ascending
  node) between the two orbits -- two objects can share an inclination but
  sit in completely different planes. tle_fetch's get_debris_field() does
  not currently return RAAN, so this is a known first-cut approximation:
  it will UNDERSTATE cost for same-inclination-different-RAAN pairs. Worth
  a note in the writeup; not blocking for a hackathon-scope router.
- Combined maneuver: the plane change is bundled entirely into whichever
  burn happens at the orbit with the LOWER circular velocity (i.e. the
  higher of the two altitudes), because that's where a plane change is
  cheapest. This is the standard practical simplification (it's what GTO
  -> GEO insertions do) rather than a numerically-optimized split across
  both burns. Optimal splitting exists in the literature but adds real
  complexity for a small further saving -- not worth it here.
"""
import math

MU_EARTH_KM3_S2 = 398600.4418   # Earth's gravitational parameter, mu = GM
R_EARTH_KM = 6378.137           # WGS84 equatorial radius -- altitude_km from
                                 # tle_fetch is geodetic elevation, so this is
                                 # the right reference radius to add it to.

SAME_ALTITUDE_TOLERANCE_KM = 1.0  # below this, treat as a pure plane change


def circular_velocity(r_km: float) -> float:
    """Velocity of a circular orbit at radius r_km (km/s)."""
    return math.sqrt(MU_EARTH_KM3_S2 / r_km)


def _vis_viva(r_km: float, a_km: float) -> float:
    """Speed (km/s) at radius r_km on an orbit with semi-major axis a_km."""
    return math.sqrt(MU_EARTH_KM3_S2 * (2.0 / r_km - 1.0 / a_km))


def plane_change_delta_v(v_km_s: float, delta_i_deg: float) -> float:
    """Cost of a pure plane change (no altitude change) at circular
    velocity v_km_s. Delta-v = 2 * v * sin(delta_i / 2)."""
    delta_i_rad = math.radians(delta_i_deg)
    return 2.0 * v_km_s * math.sin(delta_i_rad / 2.0)


def combined_burn_delta_v(v1_km_s: float, v2_km_s: float, delta_i_deg: float) -> float:
    """Cost of changing speed AND plane in a single burn (law of cosines
    on the velocity vectors). Reduces to |v1 - v2| when delta_i_deg = 0."""
    delta_i_rad = math.radians(delta_i_deg)
    val = v1_km_s**2 + v2_km_s**2 - 2.0 * v1_km_s * v2_km_s * math.cos(delta_i_rad)
    return math.sqrt(max(val, 0.0))  # clamp: guards against -1e-16 float noise at delta_i=0


def hohmann_delta_v(r1_km: float, r2_km: float) -> dict[str, float]:
    """Pure altitude-change Hohmann transfer between two circular orbits,
    no plane change. Returns both burns and the total."""
    a_transfer = (r1_km + r2_km) / 2.0

    v1_circ = circular_velocity(r1_km)
    v2_circ = circular_velocity(r2_km)
    v1_transfer = _vis_viva(r1_km, a_transfer)
    v2_transfer = _vis_viva(r2_km, a_transfer)

    dv1 = abs(v1_transfer - v1_circ)
    dv2 = abs(v2_transfer - v2_circ)

    return {
        "delta_v_burn1_km_s": dv1,
        "delta_v_burn2_km_s": dv2,
        "delta_v_total_km_s": dv1 + dv2,
    }


def transfer_delta_v(
    alt1_km: float,
    incl1_deg: float,
    alt2_km: float,
    incl2_deg: float,
) -> dict[str, float]:
    """
    Main entry point: total delta-v (km/s) to move a spacecraft from one
    debris object's orbit to another's, given altitude (km above surface)
    and inclination (deg) for each. This is what fills the N x N cost
    matrix in step 2.

    Bundles the plane change into the burn at the higher-altitude
    (lower-velocity) orbit -- see module docstring for why.
    """
    r1 = R_EARTH_KM + alt1_km
    r2 = R_EARTH_KM + alt2_km
    delta_i = abs(incl1_deg - incl2_deg)
    altitude_change_km = abs(alt2_km - alt1_km)

    # Reference-only figure: what it WOULD cost to do the plane change
    # completely separately at the lower (faster) orbit's circular
    # velocity, i.e. the naive "just add both maneuvers up" approach.
    # Reported so the combined-maneuver saving is visible, not to be used
    # as the actual cost.
    r_lo, r_hi = min(r1, r2), max(r1, r2)
    naive_plane_change = plane_change_delta_v(circular_velocity(r_lo), delta_i)

    if altitude_change_km <= SAME_ALTITUDE_TOLERANCE_KM:
        # Same shell: no altitude change, so this is just a plane change.
        v = circular_velocity((r1 + r2) / 2.0)
        total = plane_change_delta_v(v, delta_i)
        return {
            "delta_v_total_km_s": total,
            "delta_v_burn_lo_km_s": 0.0,
            "delta_v_burn_hi_km_s": total,
            "altitude_change_km": altitude_change_km,
            "inclination_change_deg": delta_i,
            "naive_separate_maneuver_km_s": naive_plane_change,
        }

    a_transfer = (r_lo + r_hi) / 2.0
    v_lo_circ = circular_velocity(r_lo)
    v_hi_circ = circular_velocity(r_hi)
    v_lo_transfer = _vis_viva(r_lo, a_transfer)
    v_hi_transfer = _vis_viva(r_hi, a_transfer)

    # Pure altitude-change burn at the lower/faster orbit (cheaper to plane-change here, so we don't).
    dv_lo = abs(v_lo_transfer - v_lo_circ)
    # Combined altitude + plane-change burn at the higher/slower orbit.
    dv_hi = combined_burn_delta_v(v_hi_transfer, v_hi_circ, delta_i)

    total = dv_lo + dv_hi

    return {
        "delta_v_total_km_s": total,
        "delta_v_burn_lo_km_s": dv_lo,
        "delta_v_burn_hi_km_s": dv_hi,
        "altitude_change_km": altitude_change_km,
        "inclination_change_deg": delta_i,
        "naive_separate_maneuver_km_s": dv_lo + naive_plane_change,
    }


if __name__ == "__main__":
    # Verification against known, well-documented real transfers.
    # If these don't match, nothing downstream can be trusted.

    print("=== Check 1: Pure Hohmann, LEO(300km) -> GEO, no plane change ===")
    r_leo = R_EARTH_KM + 300.0
    r_geo = 42164.0
    result = hohmann_delta_v(r_leo, r_geo)
    print(f"  burn1 (LEO departure):  {result['delta_v_burn1_km_s']:.3f} km/s  (textbook: ~2.44 km/s)")
    print(f"  burn2 (GEO circularize): {result['delta_v_burn2_km_s']:.3f} km/s  (textbook: ~1.47 km/s)")
    print(f"  total:                  {result['delta_v_total_km_s']:.3f} km/s  (textbook: ~3.90 km/s)")

    print("\n=== Check 2: Pure plane change, LEO circular, 90 deg (quarter-orbit) ===")
    v_leo = circular_velocity(r_leo)
    dv_plane = plane_change_delta_v(v_leo, 90.0)
    print(f"  circular velocity at 300km: {v_leo:.3f} km/s")
    print(f"  delta-v for 90deg plane change: {dv_plane:.3f} km/s  (widely cited: ~10.9-11.0 km/s -- notoriously the most expensive maneuver in LEO)")

    print("\n=== Check 3: Real-world combined maneuver -- GTO apogee burn into GEO ===")
    print("    (This is the textbook demonstration of WHY combined maneuvers matter:")
    print("     same circularization, but launch-site inclination changes the cost.)")
    r_perigee = R_EARTH_KM + 185.0  # typical GTO perigee altitude
    a_gto = (r_perigee + r_geo) / 2.0
    v_apogee_transfer = _vis_viva(r_geo, a_gto)
    v_geo_circ = circular_velocity(r_geo)

    for site, incl in [("Cape Canaveral (28.5 deg)", 28.5), ("Kourou (~6 deg)", 6.0)]:
        dv = combined_burn_delta_v(v_apogee_transfer, v_geo_circ, incl)
        print(f"  {site:<28} combined apogee burn: {dv:.3f} km/s")
    print("  Reference values commonly cited: ~1.80 km/s (Cape Canaveral, 28.5deg) vs ~1.46 km/s (Kourou, ~6deg).")
    print("  This ~0.3-0.35 km/s gap IS the reason equatorial launch sites are more efficient for GEO -- matches.")

    print("\n=== Check 4: transfer_delta_v() end-to-end, using the same GTO->GEO numbers ===")
    full = transfer_delta_v(alt1_km=185.0, incl1_deg=28.5, alt2_km=42164.0 - R_EARTH_KM, incl2_deg=0.0)
    for k, v in full.items():
        print(f"  {k}: {v:.4f}")
    print("  (Small difference from Check 3 is expected -- this treats the GTO 'departure' side as")
    print("   its own circular-orbit burn too, rather than assuming a free launch injection.)")

    print("\n=== Check 5: sanity check on debris-realistic numbers (700-1000km band) ===")
    d1 = transfer_delta_v(alt1_km=780.0, incl1_deg=74.0, alt2_km=820.0, incl2_deg=74.05)
    print(f"  Same-ish orbit, tiny inclination diff: total delta-v = {d1['delta_v_total_km_s']:.4f} km/s (should be small, cheap hop)")
    d2 = transfer_delta_v(alt1_km=780.0, incl1_deg=74.0, alt2_km=820.0, incl2_deg=98.0)
    print(f"  Same-ish altitude, large inclination diff (74 -> 98 deg): total delta-v = {d2['delta_v_total_km_s']:.4f} km/s (should be expensive)")
    print(f"    naive (separate maneuvers) would cost: {d2['naive_separate_maneuver_km_s']:.4f} km/s -- combined maneuver saves {d2['naive_separate_maneuver_km_s'] - d2['delta_v_total_km_s']:.4f} km/s")