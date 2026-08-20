#!/usr/bin/env python3
"""
audit_repo.py — repo integrity audit (read-only, no deletions/patches).

Category 1 — dead code: defined but never referenced elsewhere.
Category 2 — broken shape mismatches: frontend reads a key the backend
              never produces, or vice versa in a meaningful direction.

Also sweeps for:
  - Stale comments describing removed behavior.
  - Leftover references to the removed `risk_penalty` / `RISK_PENALTY_SCALE`
    field outside of CHANGELOG.md / docs.

Run from repo root:
    python scripts/audit_repo.py
"""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def grep_count(pattern: str, exclude_file: str | None = None) -> int:
    """Return the number of lines matching `pattern` across the whole repo,
    optionally excluding one file (the definition site)."""
    cmd = ["grep", "-r", "--include=*.py", "--include=*.jsx", "--include=*.js",
           "--include=*.md", "--include=*.txt", "-l", pattern, str(ROOT)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    files = [f for f in result.stdout.splitlines() if f]
    if exclude_file:
        files = [f for f in files if os.path.abspath(f) != os.path.abspath(exclude_file)]
    # Now count actual lines
    if not files:
        return 0
    cmd2 = ["grep", "-r", "--include=*.py", "--include=*.jsx", "--include=*.js",
            "--include=*.md", "--include=*.txt", "-c", pattern, str(ROOT)]
    result2 = subprocess.run(cmd2, capture_output=True, text=True)
    total = 0
    for line in result2.stdout.splitlines():
        fname, _, count_str = line.rpartition(":")
        if exclude_file and os.path.abspath(fname) == os.path.abspath(exclude_file):
            continue
        try:
            total += int(count_str)
        except ValueError:
            pass
    return total


def grep_lines(pattern: str, paths: list[str]) -> list[tuple[str, int, str]]:
    """Return [(filepath, lineno, line_text)] for pattern matches in paths."""
    results = []
    for p in paths:
        cmd = ["grep", "-rn", pattern, p]
        r = subprocess.run(cmd, capture_output=True, text=True)
        for line in r.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) >= 3:
                results.append((parts[0], int(parts[1]), parts[2]))
            elif len(parts) == 2:
                results.append((parts[0], 0, parts[1]))
    return results


def py_top_level_defs(filepath: str) -> list[tuple[str, int]]:
    """Return [(name, lineno)] for every top-level def/class in a .py file."""
    with open(filepath, encoding="utf-8") as f:
        source = f.read()
    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []
    result = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # Only top-level: parent is Module
            if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                   for node in [node]):
                pass
        # Direct children of module
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            result.append((node.name, node.lineno))
    return result


def js_named_exports(filepath: str) -> list[tuple[str, int]]:
    """
    Extract named function/component/export identifiers from a JS/JSX file.
    Uses regex — not a full AST parser. Returns [(name, lineno)].
    """
    results = []
    patterns = [
        r"^export\s+(?:default\s+)?function\s+(\w+)",
        r"^export\s+(?:const|let|var)\s+(\w+)",
        r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:\([^)]*\)\s*=>|\bfunction\b)",
        r"^function\s+(\w+)\s*\(",
        r"^(?:const|let|var)\s+(\w+)\s*=\s*(?:forwardRef\s*\(|React\.memo\s*\()",
    ]
    compiled = [re.compile(p) for p in patterns]
    with open(filepath, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            stripped = line.strip()
            for pat in compiled:
                m = pat.match(stripped)
                if m:
                    name = m.group(1)
                    if name not in results:
                        results.append((name, lineno))
                    break
    return results


# ---------------------------------------------------------------------------
# Category 1 — dead code scan
# ---------------------------------------------------------------------------

SKIP_ALWAYS = {
    # Python dunder/special names unlikely to be referenced externally
    "__init__", "__str__", "__repr__", "__eq__", "__hash__", "__len__",
    "__slots__", "__post_init__",
    # Single-letter or clearly internal helpers
    "app",  # FastAPI instance — referenced by uvicorn runner, not grep-able
}

def scan_dead_code_python() -> list[dict]:
    """Scan app/*.py for top-level defs with zero cross-file references."""
    findings = []
    py_files = list(Path(ROOT / "app").glob("*.py"))
    for fpath in sorted(py_files):
        defs = py_top_level_defs(str(fpath))
        for name, lineno in defs:
            if name in SKIP_ALWAYS or name.startswith("__"):
                continue
            count = grep_count(r"\b" + name + r"\b", exclude_file=str(fpath))
            findings.append({
                "kind": "python_def",
                "file": str(fpath.relative_to(ROOT)),
                "name": name,
                "lineno": lineno,
                "ref_count": count,
            })
    return findings


def scan_dead_code_js() -> list[dict]:
    """Scan frontend/src/**/*.{jsx,js} for named functions/exports with zero refs."""
    findings = []
    js_files = (
        list(Path(ROOT / "frontend/src").rglob("*.jsx")) +
        list(Path(ROOT / "frontend/src").rglob("*.js"))
    )
    for fpath in sorted(js_files):
        exports = js_named_exports(str(fpath))
        for name, lineno in exports:
            if name in SKIP_ALWAYS or name.startswith("_"):
                continue
            count = grep_count(r"\b" + name + r"\b", exclude_file=str(fpath))
            findings.append({
                "kind": "js_def",
                "file": str(fpath.relative_to(ROOT)),
                "name": name,
                "lineno": lineno,
                "ref_count": count,
            })
    return findings


# ---------------------------------------------------------------------------
# Category 2 — shape mismatch analysis
# ---------------------------------------------------------------------------

# Backend response shapes: derived by reading the code above.
# Keys produced by each endpoint/helper, listed explicitly.

BACKEND_SHAPES = {
    "/plan (via _run_plan + optimize_route)": {
        # From optimize_route() return dict
        "route", "route_details", "visited_count", "skipped_count", "skipped_names",
        "total_fuel_cost_km_s", "fuel_budget_km_s", "fuel_used_fraction",
        "total_risk_collected", "step_breakdown", "net_capacity_constrained",
        "min_depot_hop_km_s", "total_fuel_saved_km_s",
        # Added by _run_plan()
        "pool_size_used", "depot", "warning",
        # Added by /plan endpoint
        "explanation", "explanation_error", "proposals",
    },
    "/plan route_details[] entries (optimize_route)": {
        "norad_id", "name", "object_type", "removal_method", "possible_methods",
        "method_maturity", "removal_method_explanation", "risk_score",
        "arrival_time_days", "data_quality",
        # NOTE: no delta_v_km_s on route_details — only on step_breakdown
    },
    "/plan step_breakdown[] entries (_drift_walk)": {
        "from", "to", "delta_v_km_s", "arrival_time_days", "raan_drift_deg",
        "recommended_wait_days", "fuel_saved_km_s", "data_quality",
    },
    "/plan depot object": {
        "altitude_km", "inclination_deg", "raan_deg", "latitude", "longitude",
    },
    "/replan response": {
        "old_plan", "new_plan", "diff", "explanation", "overrides_applied",
    },
    "/replan diff object": {
        "added", "dropped", "fuel_delta_km_s", "risk_delta", "budget_used_delta",
        "site_change",  # optional
    },
    "/compare response": {
        "presets", "comparison_narration",
    },
    "/compare presets[] entries": {
        "label", "weights", "total_fuel_cost_km_s", "total_risk_collected",
        "visited_count", "route_details",
    },
    "/sweep-launch-window response": {
        "sweep_mode", "window", "lowest_fuel_date", "narration", "echo",
    },
    "/sweep-launch-window window[] entries": {
        "day_offset", "launch_date", "total_fuel_cost_km_s", "total_risk_collected",
        "visited_count", "data_quality", "is_pareto_optimal",  # is_pareto_optimal added by compute_pareto_frontier
        "error",  # only on failed entries
    },
    "/sweep-launch-window lowest_fuel_date": {
        "day_offset", "launch_date",
    },
    "/mission-cost response (solve_forced_route + additions)": {
        "route", "route_details", "visited_count", "total_fuel_cost_km_s",
        "total_risk_collected", "step_breakdown", "nets_carried_required",
        "total_fuel_saved_km_s", "fuel_budget_km_s",  # optional
        "warning", "depot", "explanation", "explanation_error",
    },
    "/debris/{id}/removal-methods response": {
        "norad_id", "removal_method", "reasoning", "reasoning_unavailable", "alternatives",
    },
    "/debris/{id}/removal-methods alternatives[] entries": {
        "name", "why",
    },
    "/leg explanation response": {
        "from_norad_id", "to_norad_id", "from_obj", "to_obj",
        "delta_v_km_s", "fuel_saved_km_s", "recommended_wait_days",
        "raan_drift_deg", "arrival_time_days", "explanation", "explanation_unavailable",
    },
    "/leg from_obj / to_obj (_endpoint_summary)": {
        "norad_id", "name", "data_quality", "epoch_age_days", "risk_score", "is_depot",
    },
    "/debris-field response": {
        "debris_field", "data_fetched_at", "data_stale",
    },
    "/debris/{id} response (single debris object)": {
        "norad_id", "name", "altitude_km", "latitude", "longitude",
        "inclination_deg", "raan_deg", "bstar", "rcs_m2", "object_type",
        "removal_method", "possible_methods", "method_maturity",
        "removal_method_explanation", "removal_method_explanation_source",
        "risk_score", "proximity_score", "lifetime_score",
        "size_score", "size_score_available",
        "epoch_age_days", "data_quality",
    },
}

# Frontend field reads, grouped by which backend shape they touch.
FRONTEND_READS = {
    "/plan (ReasoningPanel plan prop)": {
        "warning", "explanation", "explanation_error",
        "visited_count", "pool_size_used", "total_fuel_cost_km_s", "fuel_budget_km_s",
        "fuel_used_fraction", "total_fuel_saved_km_s", "total_risk_collected",
        "skipped_count", "skipped_names", "step_breakdown", "proposals",
    },
    "/plan step_breakdown[] (ReasoningPanel manifest + LegDetailPanel)": {
        "from", "to", "delta_v_km_s", "arrival_time_days", "raan_drift_deg",
        "recommended_wait_days", "fuel_saved_km_s", "data_quality",
    },
    "/plan route_details[] (ReplanInput, App.jsx activeRouteNoradIds)": {
        "norad_id", "name",
        # NOTE: ReplanInput comment on line 105 explicitly says no delta_v_km_s here
        # delta_v_km_s is read from step_breakdown only
    },
    "/plan depot (ReasoningPanel depotAltitudeKm/depotInclinationDeg, DebrisGlobe)": {
        "altitude_km", "inclination_deg", "raan_deg", "latitude", "longitude",
    },
    "/replan response (App.jsx)": {
        "new_plan", "overrides_applied", "diff",
    },
    "/replan diff (App.jsx)": {
        "added", "dropped", "fuel_delta_km_s", "risk_delta",
    },
    "/compare response (ComparisonPanel)": {
        "presets", "comparison_narration",
    },
    "/compare presets[] (ComparisonPanel)": {
        "label", "total_fuel_cost_km_s", "total_risk_collected", "visited_count",
    },
    "/sweep-launch-window response (LaunchWindowPanel)": {
        "sweep_mode", "window", "lowest_fuel_date", "narration",
    },
    "/sweep-launch-window window[] (LaunchWindowPanel)": {
        "launch_date", "day_offset", "total_fuel_cost_km_s", "total_risk_collected",
        "visited_count", "is_pareto_optimal", "data_quality", "error",
    },
    "/sweep-launch-window lowest_fuel_date (LaunchWindowPanel)": {
        "launch_date", "day_offset",
    },
    "/mission-cost response (CustomSelectionSummary MissionCostResult)": {
        "warning", "explanation", "explanation_error",
        "visited_count", "total_fuel_cost_km_s", "total_fuel_saved_km_s",
        "total_risk_collected", "nets_carried_required",
        "route_details", "step_breakdown",
    },
    "/mission-cost route_details[] (CustomSelectionSummary)": {
        "norad_id", "name", "removal_method",
    },
    "/mission-cost step_breakdown[] (CustomSelectionSummary)": {
        "from", "to", "delta_v_km_s", "arrival_time_days", "raan_drift_deg",
        "recommended_wait_days",
    },
    "/debris/{id}/removal-methods (DebrisInfoModal Reason tab)": {
        "removal_method", "reasoning", "reasoning_unavailable", "alternatives",
    },
    "/debris/{id}/removal-methods alternatives[] (DebrisInfoModal)": {
        "name", "why",
    },
    "/leg explanation (LegDetailPanel)": {
        "from_obj", "to_obj", "explanation", "explanation_unavailable",
    },
    "/leg from_obj / to_obj (LegDetailPanel EndpointCard)": {
        "is_depot", "name", "norad_id", "data_quality", "epoch_age_days", "risk_score",
    },
    "/debris/{id} (DebrisInfoModal Info tab)": {
        "name", "norad_id", "object_type", "data_quality", "epoch_age_days",
        "altitude_km", "latitude", "longitude",
        "inclination_deg", "raan_deg", "bstar", "rcs_m2",
        "risk_score", "proximity_score", "lifetime_score",
        "size_score", "size_score_available",
        "removal_method", "possible_methods", "method_maturity",
        "removal_method_explanation", "removal_method_explanation_source",
    },
    "/debris-field (DebrisGlobe, App.jsx)": {
        "debris_field", "data_fetched_at", "data_stale",
        # Individual debris object fields read by DebrisGlobe:
        "norad_id", "risk_score", "removal_method", "name",
        "longitude", "latitude", "altitude_km",
    },
}


def check_shape_mismatches() -> list[dict]:
    """
    Compare FRONTEND_READS against BACKEND_SHAPES for each shared endpoint.
    Returns mismatches: frontend reads a key the backend doesn't produce
    for that shape.
    """
    findings = []

    # Map endpoint label → set of backend keys
    backend_by_label: dict[str, set] = {}
    for label, keys in BACKEND_SHAPES.items():
        backend_by_label[label] = keys

    # For each frontend read group, find the matching backend shape
    endpoint_pairs = [
        ("/plan (ReasoningPanel plan prop)", "/plan (via _run_plan + optimize_route)"),
        ("/plan step_breakdown[] (ReasoningPanel manifest + LegDetailPanel)", "/plan step_breakdown[] entries (_drift_walk)"),
        ("/plan route_details[] (ReplanInput, App.jsx activeRouteNoradIds)", "/plan route_details[] entries (optimize_route)"),
        ("/plan depot (ReasoningPanel depotAltitudeKm/depotInclinationDeg, DebrisGlobe)", "/plan depot object"),
        ("/replan response (App.jsx)", "/replan response"),
        ("/replan diff (App.jsx)", "/replan diff object"),
        ("/compare response (ComparisonPanel)", "/compare response"),
        ("/compare presets[] (ComparisonPanel)", "/compare presets[] entries"),
        ("/sweep-launch-window response (LaunchWindowPanel)", "/sweep-launch-window response"),
        ("/sweep-launch-window window[] (LaunchWindowPanel)", "/sweep-launch-window window[] entries"),
        ("/sweep-launch-window lowest_fuel_date (LaunchWindowPanel)", "/sweep-launch-window lowest_fuel_date"),
        ("/mission-cost response (CustomSelectionSummary MissionCostResult)", "/mission-cost response (solve_forced_route + additions)"),
        ("/mission-cost route_details[] (CustomSelectionSummary)", "/plan route_details[] entries (optimize_route)"),
        ("/mission-cost step_breakdown[] (CustomSelectionSummary)", "/plan step_breakdown[] entries (_drift_walk)"),
        ("/debris/{id}/removal-methods (DebrisInfoModal Reason tab)", "/debris/{id}/removal-methods response"),
        ("/debris/{id}/removal-methods alternatives[] (DebrisInfoModal)", "/debris/{id}/removal-methods alternatives[] entries"),
        ("/leg explanation (LegDetailPanel)", "/leg explanation response"),
        ("/leg from_obj / to_obj (LegDetailPanel EndpointCard)", "/leg from_obj / to_obj (_endpoint_summary)"),
        ("/debris/{id} (DebrisInfoModal Info tab)", "/debris/{id} response (single debris object)"),
    ]

    for fe_label, be_label in endpoint_pairs:
        fe_keys = FRONTEND_READS.get(fe_label, set())
        be_keys = backend_by_label.get(be_label, set())
        # Frontend reads a key the backend doesn't produce → BUG (reads undefined)
        fe_only = fe_keys - be_keys
        # Backend produces a key the frontend never reads → candidate for Cat 1
        be_only = be_keys - fe_keys
        if fe_only:
            for k in sorted(fe_only):
                findings.append({
                    "kind": "frontend_reads_missing_backend_key",
                    "endpoint": fe_label,
                    "key": k,
                    "note": "Frontend reads this key but backend never produces it — reads undefined",
                })
        if be_only:
            for k in sorted(be_only):
                findings.append({
                    "kind": "backend_key_never_read_by_frontend",
                    "endpoint": be_label,
                    "key": k,
                    "note": "Backend produces this key; frontend never reads it — dead data or consumed elsewhere",
                })
    return findings


# ---------------------------------------------------------------------------
# Stale comment / risk_penalty sweep
# ---------------------------------------------------------------------------

def scan_risk_penalty_references() -> list[dict]:
    """Find every reference to risk_penalty / RISK_PENALTY_SCALE outside CHANGELOG.md."""
    findings = []
    patterns = [r"risk_penalty", r"RISK_PENALTY_SCALE", r"min_risk_penalty_scale_needed"]
    skip_files = {"CHANGELOG.md"}
    all_files = (
        list(Path(ROOT).rglob("*.py")) +
        list(Path(ROOT).rglob("*.jsx")) +
        list(Path(ROOT).rglob("*.js")) +
        list(Path(ROOT).rglob("*.md")) +
        list(Path(ROOT).rglob("*.txt"))
    )
    for fpath in all_files:
        rel = str(fpath.relative_to(ROOT))
        if fpath.name in skip_files:
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for pat in patterns:
            for m in re.finditer(pat, text):
                lineno = text[:m.start()].count("\n") + 1
                line = text.splitlines()[lineno - 1] if lineno <= len(text.splitlines()) else ""
                findings.append({
                    "kind": "risk_penalty_reference",
                    "file": rel,
                    "lineno": lineno,
                    "pattern": pat,
                    "line": line.strip(),
                })
    return findings


def scan_stale_comments() -> list[dict]:
    """
    Heuristic scan for comments that describe behavior removed per CHANGELOG.
    Looks for specific removed concepts: risk_penalty_scale, min_risk_penalty_scale_needed,
    and the old "risk-weighted objective" phrasing in code comments (not CHANGELOG/README).
    Also checks for any comment referencing RISK_SCALE or risk_penalty in code files.
    """
    findings = []
    code_files = (
        list(Path(ROOT / "app").rglob("*.py")) +
        list(Path(ROOT / "tests").rglob("*.py")) +
        list(Path(ROOT / "frontend/src").rglob("*.jsx")) +
        list(Path(ROOT / "frontend/src").rglob("*.js"))
    )
    stale_patterns = [
        r"risk.penalty.scale",
        r"RISK_PENALTY_SCALE",
        r"min_risk_penalty_scale_needed",
        r"risk-weighted objective",
        r"risk_score.*penalty",
    ]
    for fpath in code_files:
        try:
            lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            # Only flag comment lines
            is_comment = stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("*")
            if not is_comment:
                continue
            for pat in stale_patterns:
                if re.search(pat, stripped, re.IGNORECASE):
                    findings.append({
                        "kind": "stale_comment",
                        "file": str(fpath.relative_to(ROOT)),
                        "lineno": lineno,
                        "pattern": pat,
                        "line": stripped,
                    })
    return findings


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 72)
    print("REPO INTEGRITY AUDIT — read-only, no changes made")
    print("=" * 72)

    # ── Category 1: Dead code ─────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("CATEGORY 1 — DEAD CODE (defined, zero cross-file references)")
    print("─" * 72)

    py_defs = scan_dead_code_python()
    js_defs = scan_dead_code_js()
    all_defs = py_defs + js_defs

    zero_ref = [d for d in all_defs if d["ref_count"] == 0]
    low_ref  = [d for d in all_defs if 1 <= d["ref_count"] <= 1]

    print(f"\n  Scanned {len(py_defs)} Python top-level defs and {len(js_defs)} JS/JSX named functions.")
    print(f"  Zero references (outside own file): {len(zero_ref)}")
    print(f"  Exactly 1 reference (may be only self-import): {len(low_ref)}")

    if zero_ref:
        print("\n  [ZERO-REF — candidates for dead code]")
        for d in sorted(zero_ref, key=lambda x: (x["file"], x["lineno"])):
            print(f"    {d['file']}:{d['lineno']}  {d['name']}  (refs=0)")
    else:
        print("\n  [No zero-ref definitions found]")

    if low_ref:
        print("\n  [ONE-REF — flag for human review]")
        for d in sorted(low_ref, key=lambda x: (x["file"], x["lineno"])):
            print(f"    {d['file']}:{d['lineno']}  {d['name']}  (refs={d['ref_count']})")

    # ── Category 2: Shape mismatches ─────────────────────────────────────
    print("\n" + "─" * 72)
    print("CATEGORY 2 — BROKEN SHAPE MISMATCHES")
    print("─" * 72)

    mismatches = check_shape_mismatches()
    fe_broken = [m for m in mismatches if m["kind"] == "frontend_reads_missing_backend_key"]
    be_only   = [m for m in mismatches if m["kind"] == "backend_key_never_read_by_frontend"]

    print(f"\n  [2a] Frontend reads a key the backend never produces (BROKEN — reads undefined):")
    if fe_broken:
        for m in fe_broken:
            print(f"    ENDPOINT: {m['endpoint']}")
            print(f"      KEY:  {m['key']}")
            print(f"      NOTE: {m['note']}")
    else:
        print("    [none found]")

    print(f"\n  [2b] Backend produces a key the frontend never reads (dead data / consumed server-side):")
    if be_only:
        for m in be_only:
            print(f"    ENDPOINT: {m['endpoint']}")
            print(f"      KEY:  {m['key']}")
            print(f"      NOTE: {m['note']}")
    else:
        print("    [none found]")

    # ── risk_penalty sweep ────────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("RISK_PENALTY / risk_penalty SWEEP (should be fully removed from code)")
    print("─" * 72)

    rp_refs = scan_risk_penalty_references()
    if rp_refs:
        for r in rp_refs:
            print(f"  {r['file']}:{r['lineno']}  [{r['pattern']}]  {r['line']}")
    else:
        print("  [No risk_penalty references found outside CHANGELOG.md]")

    # ── Stale comment scan ────────────────────────────────────────────────
    print("\n" + "─" * 72)
    print("STALE COMMENT SCAN (comments describing removed behavior)")
    print("─" * 72)

    stale = scan_stale_comments()
    if stale:
        for s in stale:
            print(f"  {s['file']}:{s['lineno']}  [{s['pattern']}]  {s['line']}")
    else:
        print("  [No stale comments found]")

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "=" * 72)
    print("AUDIT SUMMARY")
    print("=" * 72)
    print(f"  Category 1 — zero-ref definitions:     {len(zero_ref)}")
    print(f"  Category 1 — single-ref (flag only):   {len(low_ref)}")
    print(f"  Category 2 — frontend reads missing key: {len(fe_broken)}")
    print(f"  Category 2 — backend-only keys (unused): {len(be_only)}")
    print(f"  risk_penalty references in code files: {len(rp_refs)}")
    print(f"  Stale comment hits:                    {len(stale)}")
    print()
    print("  Nothing was deleted or patched. Review findings above before acting.")
    print()


if __name__ == "__main__":
    main()
