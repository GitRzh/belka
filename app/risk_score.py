"""
Module A (continued): Risk scoring.

Blends two factors, each normalized to [0, 1], into one risk_score per object:

1. Proximity/congestion risk — how many other tracked objects share a similar
   altitude + inclination shell. More neighbors = more collision opportunities.
   This is exactly what makes the Cosmos-2251/Iridium-33/Fengyun-1C clouds
   dangerous: thousands of fragments packed into the same narrow band.

2. Lifetime risk — how long the object stays a hazard before atmospheric drag
   pulls it down. Approximated from BSTAR (drag term, already present in every
   Celestrak OMM record — no extra API call needed). Low drag -> long
   lifetime -> higher long-term risk.

Weights are tunable and meant to be overridden later by the /replan LLM
constraint parser (e.g. "prioritize riskiest debris" -> bump w_proximity).
"""
from typing import Any

ALT_WINDOW_KM = 25.0     # objects within this altitude band count as "neighbors"
INCL_WINDOW_DEG = 5.0    # and within this inclination band
MAX_EXPECTED_NEIGHBORS = 30.0  # neighbor count that saturates proximity_score at 1.0

DEFAULT_WEIGHTS = {"proximity": 0.6, "lifetime": 0.4}


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


def score_debris_field(
    objects: list[dict[str, Any]],
    weights: dict[str, float] = DEFAULT_WEIGHTS,
) -> list[dict[str, Any]]:
    """Add proximity_score, lifetime_score, and blended risk_score to each object.
    Returns a new list sorted by risk_score descending (riskiest first)."""
    if not objects:
        return []

    proximity = _proximity_scores(objects)
    lifetime = _lifetime_scores(objects)
    w_prox = weights.get("proximity", DEFAULT_WEIGHTS["proximity"])
    w_life = weights.get("lifetime", DEFAULT_WEIGHTS["lifetime"])

    scored = []
    for obj, p_score, l_score in zip(objects, proximity, lifetime):
        risk = w_prox * p_score + w_life * l_score
        scored.append({
            **obj,
            "proximity_score": round(p_score, 4),
            "lifetime_score": round(l_score, 4),
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

    print(f"Scored {len(scored)} objects. Top 5 riskiest:")
    for obj in scored[:5]:
        print(
            f"  {obj['name']:<25} risk={obj['risk_score']:.3f} "
            f"(proximity={obj['proximity_score']:.3f}, lifetime={obj['lifetime_score']:.3f}) "
            f"@ {obj['altitude_km']}km"
        )