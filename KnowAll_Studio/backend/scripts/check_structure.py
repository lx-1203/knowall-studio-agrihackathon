#!/usr/bin/env python
"""Structural invariants that were previously checked by hand.

Why this exists
---------------
Rounds 21-22 audited the accumulated work and found that several "conclusions"
lived only in prose: nobody re-ran them. Two of those checks are cheap,
deterministic and dependency-free, so they are codified here rather than being
re-run from memory:

1. **Layering.** `app/core/*` must not import `app/api/*`, and `app/models/*`
   must not import from either. A backend can be made to "work" with a reversed
   edge (the import succeeds) while quietly making the layers untestable and
   tangled. Round 22 verified this was clean; nothing kept it clean.

2. **Docs numbering.** `optimization-report.md` §N and
   `optimization-backlog.md` B<N> are meant to be 1:1. Both files are appended to
   every round, which is exactly the situation where they drift apart silently.

What this deliberately does NOT check
-------------------------------------
* Import *cycles* — importing `app.main` in the test suite would surface those
  immediately, and a cycle detector here would duplicate the interpreter.
* Style (line length, unused imports). Those are a different axis and, for unused
  imports in particular, the measured win was ~0 bytes (round 20).

Usage
-----
    python scripts/check_structure.py [--json]

Run from anywhere; paths are resolved relative to this file.
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent.parent
APP = BACKEND_ROOT / "app"

REPORT = REPO_ROOT / "KnowAll_Studio" / "docs" / "optimization-report.md"
BACKLOG = REPO_ROOT / "KnowAll_Studio" / "docs" / "optimization-backlog.md"

# Layers that must never be imported *from* the ones on the left.
FORBIDDEN_EDGES = {
    "core": {"api"},
    "models": {"api", "core"},
}


def _app_imports(path: pathlib.Path) -> set[str]:
    """Dotted `app.*` module names imported by `path`."""
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app"):
            found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app"):
                    found.add(alias.name)
    return found


def check_layering() -> list[dict]:
    violations: list[dict] = []
    for py in sorted(APP.rglob("*.py")):
        if "__pycache__" in str(py):
            continue
        rel = py.relative_to(BACKEND_ROOT).as_posix()
        parts = py.relative_to(APP).parts
        if len(parts) < 2:
            continue
        src_layer = parts[0]
        forbidden = FORBIDDEN_EDGES.get(src_layer)
        if not forbidden:
            continue
        for module in _app_imports(py):
            bits = module.split(".")
            if len(bits) >= 2 and bits[1] in forbidden:
                violations.append(
                    {"path": rel, "layer": src_layer, "imports": module, "forbidden": bits[1]}
                )
    return violations


def check_doc_numbering() -> dict:
    if not REPORT.exists() or not BACKLOG.exists():
        return {"skipped": "docs not found", "report": [], "backlog": [], "missing": []}

    report_nums = sorted(
        int(m) for m in re.findall(r"^## (\d+)\. ", REPORT.read_text(encoding="utf-8"), re.M)
    )
    backlog_nums = sorted(
        int(m)
        for m in re.findall(r"^### \u2705 B(\d+)\. ", BACKLOG.read_text(encoding="utf-8"), re.M)
    )
    missing = sorted(set(report_nums) ^ set(backlog_nums))
    # Gaps inside a sequence are drift too, not just mismatched set membership.
    gaps = [
        n
        for nums in (report_nums, backlog_nums)
        for n in range(1, (max(nums) if nums else 0) + 1)
        if n not in nums
    ]
    return {
        "report": report_nums,
        "backlog": backlog_nums,
        "missing": sorted(set(missing) | set(gaps)),
    }


def main() -> int:
    as_json = "--json" in sys.argv
    layering = check_layering()
    numbering = check_doc_numbering()
    numbering_bad = bool(numbering.get("missing"))

    if as_json:
        print(json.dumps({"layering": layering, "docs": numbering}, indent=2))
    else:
        print("--- layering (core -> api, models -> api/core) ---")
        if layering:
            for v in layering:
                print(f"  VIOLATION {v['path']}: {v['layer']} imports {v['imports']}")
        else:
            print("  OK  no forbidden import edges")

        print("--- docs numbering (report §N <-> backlog BN) ---")
        if numbering.get("missing") == []:
            print(
                f"  OK  {len(numbering['report'])} report sections, "
                f"{len(numbering['backlog'])} backlog items, 1:1"
            )
        elif "skipped" in numbering:
            print(f"  SKIP {numbering['skipped']}")
        else:
            print(f"  MISMATCH missing/misnumbered: {numbering['missing']}")
            print(f"    report : {numbering['report']}")
            print(f"    backlog: {numbering['backlog']}")

    return 1 if (layering or numbering_bad) else 0


if __name__ == "__main__":
    raise SystemExit(main())
