"""
Module: removal-method recommendation.

Pure lookup table, no LLM. Classifies each debris object into a removal
method using only fields that already exist in the data as fetched from
Celestrak (tle_fetch.py) -- no faked/invented fields.

Two real signals:
1. "DEB" in name -> fragment; absent -> intact/parent object.
   e.g. "COSMOS 2251 DEB (33762)" = fragment, "COSMOS 2251 (22675)" = parent.
2. bstar (drag term, already on every object) as an area-to-mass proxy:
   higher bstar = more drag per unit mass = smaller/lighter fragment;
   lower bstar = denser/larger object. Threshold is the MEDIAN bstar among
   fragments in the current batch (not a hardcoded constant), since bstar
   magnitudes vary by altitude band and debris population -- same
   batch-relative approach risk_score.py already uses for lifetime_score.

Method mapping (grounded in real debris-removal literature -- Astroscale/
ClearSpace-style method selection):
- intact object          -> robotic arm or net capture (identifiable
                             structure exists to grapple/dock)
- larger fragment (DEB,
  below-median bstar)     -> net capture (irregular shape rules out
                             docking, but large enough to be a viable
                             single target)
- small fragment (DEB,
  above-median bstar)     -> monitor/bulk mitigation only. Honest
                             limitation, not a shortcut: most cm-scale
                             debris genuinely isn't captured one at a time
                             in real missions today.
"""
import statistics
from typing import Any

OBJECT_TYPE_INTACT = "intact"
OBJECT_TYPE_FRAGMENT = "fragment"

METHOD_ROBOTIC_ARM_OR_NET = "robotic_arm_or_net_capture"
METHOD_NET_CAPTURE = "net_capture"
METHOD_MONITOR_ONLY = "monitor_only"

# Individual technique identifiers. removal_method (above) can bundle two of
# these under one ambiguous label for intact objects -- possible_methods /
# method_maturity below unpack that bundle so maturity isn't hidden behind
# a single label implying equal confidence in both techniques.
TECHNIQUE_ROBOTIC_ARM = "robotic_arm"
TECHNIQUE_NET_CAPTURE = "net_capture"
TECHNIQUE_MONITOR_ONLY = "monitor_only"

# Real-world flight status per technique (see README "Real-World Grounding"):
# - net_capture: flight-demonstrated (RemoveDEBRIS, 2018-2019).
# - robotic_arm: conceptual -- no flown precedent capturing UNCOOPERATIVE
#   debris (ClearSpace-1 is single-target/unflown; ELSA-M's reusable
#   capture only works on targets with a pre-installed docking plate).
# - monitor_only: not a capture technique at all -- ground-based tracking
#   of this population is already real and operational (Space Surveillance
#   Network), just not something this system routes a spacecraft to.
MATURITY_FLIGHT_DEMONSTRATED = "flight_demonstrated"
MATURITY_CONCEPTUAL = "conceptual"
MATURITY_OPERATIONAL = "operational"


def classify_object_type(name: str) -> str:
    """'DEB' in the name marks a tracked fragment; its absence marks an
    intact/parent object. This is a real Celestrak naming convention, not
    a heuristic we're inventing."""
    return OBJECT_TYPE_FRAGMENT if "DEB" in name.upper() else OBJECT_TYPE_INTACT


def _fragment_bstar_threshold(objects: list[dict[str, Any]]) -> float:
    """Median |bstar| among fragments in this batch. Batch-relative on
    purpose -- bstar scale shifts with altitude band and which debris
    clouds are in play, so a fixed constant would drift out of calibration
    the moment DEBRIS_GROUPS or the altitude band changes."""
    fragment_bstars = [
        abs(o.get("bstar", 0.0))
        for o in objects
        if classify_object_type(o.get("name", "")) == OBJECT_TYPE_FRAGMENT
    ]
    if not fragment_bstars:
        return 0.0
    return statistics.median(fragment_bstars)


def _removal_method_for(object_type: str, bstar: float, threshold: float) -> dict[str, Any]:
    """Returns removal_method (bare label, unchanged for backward compat)
    plus possible_methods/method_maturity -- the individual technique(s)
    that label may bundle, each rated by real flight status. Intact
    objects get an honest two-technique hedge (data can't discriminate
    further); fragments get a single-item list either way."""
    if object_type == OBJECT_TYPE_INTACT:
        return {
            "removal_method": METHOD_ROBOTIC_ARM_OR_NET,
            "possible_methods": [TECHNIQUE_ROBOTIC_ARM, TECHNIQUE_NET_CAPTURE],
            "method_maturity": {
                TECHNIQUE_ROBOTIC_ARM: MATURITY_CONCEPTUAL,
                TECHNIQUE_NET_CAPTURE: MATURITY_FLIGHT_DEMONSTRATED,
            },
        }
    # object_type == fragment
    if abs(bstar) > threshold:
        return {
            "removal_method": METHOD_MONITOR_ONLY,
            "possible_methods": [TECHNIQUE_MONITOR_ONLY],
            "method_maturity": {TECHNIQUE_MONITOR_ONLY: MATURITY_OPERATIONAL},
        }
    return {
        "removal_method": METHOD_NET_CAPTURE,
        "possible_methods": [TECHNIQUE_NET_CAPTURE],
        "method_maturity": {TECHNIQUE_NET_CAPTURE: MATURITY_FLIGHT_DEMONSTRATED},
    }


def add_removal_methods(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add object_type and removal_method to every object in the batch.
    Returns a new list (does not mutate input), same shape convention as
    score_debris_field() -- pure additive fields, everything else passes
    through unchanged."""
    if not objects:
        return []

    threshold = _fragment_bstar_threshold(objects)
    enriched = []
    for obj in objects:
        obj_type = classify_object_type(obj.get("name", ""))
        method_info = _removal_method_for(obj_type, obj.get("bstar", 0.0), threshold)
        enriched.append({
            **obj,
            "object_type": obj_type,
            **method_info,  # removal_method, possible_methods, method_maturity
        })
    return enriched


if __name__ == "__main__":
    # Quick sanity test with synthetic objects shaped like real Celestrak
    # records -- mirrors the synthetic-data pattern used in cost_matrix.py
    # and optimizer.py's own __main__ blocks.
    sample = [
        {"norad_id": 1, "name": "COSMOS 2251 (22675)", "bstar": 0.00002},   # intact
        {"norad_id": 2, "name": "COSMOS 2251 DEB (33762)", "bstar": 0.00001},  # low-bstar frag
        {"norad_id": 3, "name": "COSMOS 2251 DEB (33999)", "bstar": 0.00009},  # high-bstar frag
        {"norad_id": 4, "name": "IRIDIUM 33 DEB (34201)", "bstar": 0.00005},   # mid-bstar frag
        {"norad_id": 5, "name": "FENGYUN 1C (25730)", "bstar": 0.00003},       # intact
    ]

    result = add_removal_methods(sample)
    print(f"Classified {len(result)} objects (fragment bstar threshold computed from batch median):\n")
    for o in result:
        print(f"  {o['name']:<28} type={o['object_type']:<9} bstar={o['bstar']:.5f} -> {o['removal_method']:<26} possible={o['possible_methods']} maturity={o['method_maturity']}")

    # Sanity checks
    assert result[0]["object_type"] == OBJECT_TYPE_INTACT
    assert result[0]["removal_method"] == METHOD_ROBOTIC_ARM_OR_NET
    assert result[4]["object_type"] == OBJECT_TYPE_INTACT
    assert all(o["object_type"] == OBJECT_TYPE_FRAGMENT for o in [result[1], result[2], result[3]])

    # New this round: removal_method stays a bare string (backward compat),
    # possible_methods/method_maturity unpack it.
    assert result[0]["possible_methods"] == [TECHNIQUE_ROBOTIC_ARM, TECHNIQUE_NET_CAPTURE]
    assert result[0]["method_maturity"] == {
        TECHNIQUE_ROBOTIC_ARM: MATURITY_CONCEPTUAL,
        TECHNIQUE_NET_CAPTURE: MATURITY_FLIGHT_DEMONSTRATED,
    }
    for o in result:
        if o["removal_method"] in (METHOD_NET_CAPTURE, METHOD_MONITOR_ONLY):
            assert len(o["possible_methods"]) == 1
            assert o["possible_methods"][0] in o["method_maturity"]
    print("\nSanity checks passed: intact objects -> robotic_arm_or_net, fragments split by median bstar, "
          "possible_methods/method_maturity present and consistent with removal_method on every object.")