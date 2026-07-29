"""
Module A (continued): Risk scoring.

Blends up to three factors, each normalized to [0, 1], into one risk_score
per object:

1. Proximity/congestion risk — how many other tracked objects share a similar
   altitude + inclination shell. More neighbors = more collision opportunities.
   This is exactly what makes the Cosmos-2251/Iridium-33/Fengyun-1C clouds
   dangerous: thousands of fragments packed into the same narrow band.

2. Lifetime risk — how long the object stays a hazard before atmospheric drag
   pulls it down. Approximated from BSTAR (drag term, already present in every
   Celestrak OMM record — no extra API call needed). Low drag -> long
   lifetime -> higher long-term risk.

3. Size risk — larger debris causes more severe collisions. Derived from
   radar cross-section (rcs_m2, m²) sourced from CelesTrak SATCAT. Only
   available for catalogued objects that have RCS data; objects without
   RCS contribute to the blend using only proximity + lifetime, renormalized
   to their combined weight.

Weights are tunable and meant to be overridden later by the /replan LLM
constraint parser (e.g. "prioritize riskiest debris" -> bump w_proximity).
"""
from typing import Any

RISK_SCORE_DISCLAIMER = (
    "risk_score is a relative ranking within this scored batch, not an "
    "absolute collision probability or a NASA-standard conjunction metric. "
    "It blends local congestion (proximity_score), estimated residual orbital "
    "lifetime from drag (lifetime_score), and physical size (size_score, from "
    "CelesTrak SATCAT radar cross-section). size_score is only available for "
    "objects with catalogued RCS data — coverage is partial, so objects without "
    "RCS are scored on proximity + lifetime only, renormalized to their combined "
    "weight. Useful for prioritizing which objects to target first, not for "
    "reporting probability-of-collision."
)

ALT_WINDOW_KM = 25.0     # objects within this altitude band count as "neighbors"
INCL_WINDOW_DEG = 5.0    # and within this inclination band
MAX_EXPECTED_NEIGHBORS = 30.0  # neighbor count that saturates proximity_score at 1.0

DEFAULT_WEIGHTS = {"proximity": 0.45, "lifetime": 0.30, "size": 0.25}


def _proximity_scores(objects: list[dict[str, Any]]) -> list[float]:
    """For each object, count neighbors within the altitude/inclination window
    and normalize to [0, 1]. O(n^2) — fine for a few hundred objects."""
    n = len(objects)
    scores = [0.0] * n

    for i in range(n):
        alt_i = objects[i]["altitude_km"]
        incl_i = objects[i]["inclination_deg"]
        neighbor_count = 0

        for j in range(n):
            if i == j:
                continue
            alt_j = objects[j]["altitude_km"]
            incl_j = objects[j]["inclination_deg"]
            if abs(alt_i - alt_j) <= ALT_WINDOW_KM and abs(incl_i - incl_j) <= INCL_WINDOW_DEG:
                neighbor_count += 1

        scores[i] = min(1.0, neighbor_count / MAX_EXPECTED_NEIGHBORS)

    return scores


def _lifetime_scores(objects: list[dict[str, Any]]) -> list[float]:
    """Normalize |BSTAR| across the current dataset, then invert: low drag
    (long remaining lifetime) -> high risk score."""
    bstars = [abs(o.get("bstar", 0.0)) for o in objects]

    if not bstars:
        return []

    lo, hi = min(bstars), max(bstars)
    span = hi - lo if hi > lo else 1e-12  # avoid divide-by-zero if all equal

    scores = []
    for b in bstars:
        drag_norm = (b - lo) / span       # 0 = lowest drag, 1 = highest drag
        scores.append(1.0 - drag_norm)    # invert: low drag = high lifetime risk

    return scores


def _size_scores(objects: list[dict[str, Any]]) -> list[float | None]:
    """Min-max normalize rcs_m2 across objects that have a non-null value.
    Returns None for objects with rcs_m2 is None — do NOT default to 0 or mean.
    Larger RCS -> higher size risk (no inversion needed, unlike lifetime)."""
    rcs_values = [
        o.get("rcs_m2")
        for o in objects
        if o.get("rcs_m2") is not None
    ]

    if not rcs_values:
        return [None] * len(objects)

    lo = min(rcs_values)
    hi = max(rcs_values)
    span = hi - lo if hi > lo else 1e-12  # avoid divide-by-zero if all equal

    scores: list[float | None] = []
    for o in objects:
        rcs = o.get("rcs_m2")
        if rcs is None:
            scores.append(None)
        else:
            scores.append((rcs - lo) / span)

    return scores


def score_debris_field(
    objects: list[dict[str, Any]],
    weights: dict[str, float] = DEFAULT_WEIGHTS,
) -> list[dict[str, Any]]:
    """Add proximity_score, lifetime_score, size_score, and blended risk_score
    to each object.  Returns a new list sorted by risk_score descending.

    When size_score is None (no RCS data), the blend falls back to proximity +
    lifetime renormalized to their combined weight so the output remains in
    [0, 1] regardless of coverage."""
    if not objects:
        return []

    proximity = _proximity_scores(objects)
    lifetime = _lifetime_scores(objects)
    size = _size_scores(objects)
    w_prox = weights.get("proximity", DEFAULT_WEIGHTS["proximity"])
    w_life = weights.get("lifetime", DEFAULT_WEIGHTS["lifetime"])
    w_size = weights.get("size", DEFAULT_WEIGHTS["size"])

    scored = []
    for obj, p_score, l_score, s_score in zip(objects, proximity, lifetime, size):
        if s_score is None:
            # No RCS — renormalize over proximity + lifetime only so risk
            # stays in [0, 1] and isn't artificially deflated by a missing term.
            pl_sum = w_prox + w_life
            risk = (w_prox * p_score + w_life * l_score) / pl_sum
        else:
            risk = w_prox * p_score + w_life * l_score + w_size * s_score

        scored.append({
            **obj,
            "proximity_score": round(p_score, 4),
            "lifetime_score": round(l_score, 4),
            "size_score": round(s_score, 4) if s_score is not None else None,
            "size_score_available": s_score is not None,
            "rcs_m2": obj.get("rcs_m2"),  # pass-through; may already be present
            "risk_score": round(risk, 4),
        })

    scored.sort(key=lambda o: o["risk_score"], reverse=True)
    return scored


if __name__ == "__main__":
    # Quick end-to-end test: pull real debris, score it, show the riskiest.
    # NOTE: needs real internet access to celestrak.org.
    try:
        from .tle_fetch import get_debris_field  # when imported as part of the app package
    except ImportError:
        from tle_fetch import get_debris_field  # pyright: ignore[reportImplicitRelativeImport]

    debris = get_debris_field()
    scored = score_debris_field(debris)

    with_rcs = sum(1 for o in scored if o["size_score_available"])
    print(f"Scored {len(scored)} objects ({with_rcs} with RCS/size_score). Top 5 riskiest:")
    for obj in scored[:5]:
        size_str = f"size={obj['size_score']:.3f}" if obj["size_score_available"] else "size=n/a"
        print(
            f"  {obj['name']:<25} risk={obj['risk_score']:.3f} "
            f"(proximity={obj['proximity_score']:.3f}, lifetime={obj['lifetime_score']:.3f}, {size_str}) "
            f"@ {obj['altitude_km']}km  rcs={obj['rcs_m2']}m²"
        )