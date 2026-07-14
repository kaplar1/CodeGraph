#!/usr/bin/env python3
"""
smoke_test.py — Run before deploying the skill on a real repo.

The pytest suite in tests/ checks the extraction logic against small
synthetic fixtures. This script instead runs the real pipeline end-to-end
against your *actual* codebase and checks for the kind of problems that
only show up at real scale: silent zero-edge failures, runaway timing,
broken JSON, and anything that looks like it might leak sensitive names
into a graph you're about to show teammates.

Usage:
  python3 scripts/smoke_test.py /path/to/your/repo [--functions]

Exit code is 0 even on warnings — this is a report, not a gate. Read it.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_graph as bg
import render_dashboard as rd

SECRET_LIKE = re.compile(
    r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|api[_-]?key|secret[_-]?key|password\s*=",
    re.I,
)


def warn(msg):
    print(f"  \u26a0  {msg}")


def ok(msg):
    print(f"  \u2713  {msg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--functions", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}")
        return 1

    print(f"Running against: {root}\n")

    t0 = time.time()
    graph = bg.build_graph(str(root), include_functions=args.functions)
    elapsed = time.time() - t0

    stats = graph["stats"]
    print(f"Files analyzed: {stats['file_count']}")
    print(f"Edges found:    {stats['edge_count']}")
    print(f"Languages:      {stats['languages']}")
    print(f"Time:           {elapsed:.1f}s\n")

    print("Checks:")

    # 1. Did we find any source files at all?
    if stats["file_count"] == 0:
        warn("No source files matched. Check you pointed this at the right "
             "directory, and that the languages here are ones build_graph.py covers.")
    else:
        ok("Found source files.")

    # 2. Zero edges with many files usually means import resolution isn't
    #    matching this repo's layout (unusual build config, monorepo path
    #    aliases, etc.) rather than a genuinely edge-free codebase.
    if stats["file_count"] > 5 and stats["edge_count"] == 0:
        warn("Zero edges found across multiple files — resolution is likely "
             "not matching this repo's import/include style. Spot-check a "
             "file you know imports another and see why it didn't resolve "
             "(open build_graph.py's resolve_import for your language).")
    elif stats["file_count"] > 5:
        ok("Found dependency edges.")

    # 3. Sanity-check the "most central file" isn't a runaway due to a
    #    resolution bug pointing everything at one wrong target.
    file_nodes = [n for n in graph["nodes"] if n["type"] == "file"]
    if file_nodes:
        top = max(file_nodes, key=lambda n: n.get("criticality", 0))
        share = top.get("criticality", 0) / max(1, stats["file_count"])
        if share > 0.7:
            warn(f"'{top['id']}' is depended on by {share:.0%} of all files — "
                 f"double check that's architecturally real and not every "
                 f"import silently resolving to the same wrong node.")
        else:
            ok(f"Most central file looks plausible: {top['id']} "
               f"({top.get('criticality', 0)} dependents).")

    # 4. Timing sanity for CI/pre-commit use.
    if elapsed > 30:
        warn(f"Took {elapsed:.0f}s — fine for a one-off, but consider whether "
             f"you want this in a pre-commit hook vs. CI-only or on-demand.")
    else:
        ok(f"Fast enough for routine re-runs ({elapsed:.1f}s).")

    # 5. Courtesy scan for secret-shaped strings in what will become a
    #    shareable JSON/dashboard. This only scans file paths and
    #    def/class/function names (the graph never embeds file contents),
    #    but a badly named file or function is still worth a heads-up
    #    before you hand the dashboard to teammates.
    haystack = json.dumps(graph)
    hits = set(m.group(0) for m in SECRET_LIKE.finditer(haystack))
    if hits:
        warn(f"Found secret-shaped text in file/def names: {sorted(hits)[:5]} "
             f"— the graph never includes file *contents*, but review before "
             f"sharing the dashboard outside the team.")
    else:
        ok("No obviously secret-shaped strings in file/def names.")

    # 6. Confirm the dashboard actually renders and round-trips.
    html = rd.TEMPLATE.replace("__GRAPH_JSON__", json.dumps(graph))
    m = re.search(r"const GRAPH = (.*);\n", html)
    try:
        json.loads(m.group(1)) if m else (_ for _ in ()).throw(ValueError())
        ok("Dashboard renders with valid embedded JSON.")
    except Exception:
        warn("Dashboard JSON embed failed to round-trip — investigate before sharing.")

    print(f"\nTop 10 most depended-on files:")
    for n in sorted(file_nodes, key=lambda n: n.get("criticality", 0), reverse=True)[:10]:
        print(f"  {n.get('criticality', 0):>3}  {n['id']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
