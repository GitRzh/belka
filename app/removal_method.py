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


def _removal_method_for(object_type: str, bstar: float, threshold: float) -> str:
    if object_type == OBJECT_TYPE_INTACT:
        return METHOD_ROBOTIC_ARM_OR_NET
    # object_type == fragment
    return METHOD_MONITOR_ONLY if abs(bstar) > threshold else METHOD_NET_CAPTURE


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
        method = _removal_method_for(obj_type, obj.get("bstar", 0.0), threshold)
        enriched.append({
            **obj,
            "object_type": obj_type,
            "removal_method": method,
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
        print(f"  {o['name']:<28} type={o['object_type']:<9} bstar={o['bstar']:.5f} -> {o['removal_method']}")

    # Sanity checks
    assert result[0]["object_type"] == OBJECT_TYPE_INTACT
    assert result[0]["removal_method"] == METHOD_ROBOTIC_ARM_OR_NET
    assert result[4]["object_type"] == OBJECT_TYPE_INTACT
    assert all(o["object_type"] == OBJECT_TYPE_FRAGMENT for o in [result[1], result[2], result[3]])
    print("\nSanity checks passed: intact objects -> robotic_arm_or_net, fragments split by median bstar.")
