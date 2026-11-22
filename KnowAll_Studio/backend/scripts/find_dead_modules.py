#!/usr/bin/env python
"""Find backend modules that nothing imports (dead-module candidates).

Why this exists
---------------
"Is this module used?" is usually answered by grepping for the filename, which
is unreliable: a module can be imported only by a module that is itself dead.
This builds the actual import graph instead.

Python is dynamic, so results are *candidates*, not verdicts. Two adjustments
keep the signal usable (a tool that cries wolf gets ignored):

* `__init__.py` files are skipped. They are package markers; nothing ever
  "imports" them explicitly, yet removing one breaks its package. They are also
  how `importlib.import_module("pkg.child")` finds the parent.
* Modules reached only through `importlib` are listed in DYNAMIC below, because
  no static analysis can see that edge.

Usage
-----
    python scripts/find_dead_modules.py [--json]

Run from anywhere; paths are resolved relative to this file.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = BACKEND_ROOT / "app"

# Entry points — never dead by definition.
ENTRY = {"app.main"}

# Loaded via importlib in app/core/rag_pipeline/factory.py (_PROVIDERS registry).
DYNAMIC = {
    "app.core.rag_pipeline.providers.chromadb_pipeline",
    "app.core.rag_pipeline.providers.graphrag_pipeline",
    "app.core.rag_pipeline.providers.llamaindex_pipeline",
}


def module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(BACKEND_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def build_imports(files: dict[str, pathlib.Path]) -> set[str]:
    imported: set[str] = set()
    for mod, path in files.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover - surfaced to the caller
            print(f"SYNTAX ERROR {path}: {exc}", file=sys.stderr)
            continue
        pkg = mod if path.name == "__init__.py" else mod.rsplit(".", 1)[0]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = pkg
                    for _ in range(node.level - 1):
                        base = base.rsplit(".", 1)[0] if "." in base else base
                    target = f"{base}.{node.module}" if node.module else base
                else:
                    target = node.module or ""
                if target:
                    imported.add(target)
                    imported.update(f"{target}.{a.name}" for a in node.names)
    return imported


def main() -> int:
    as_json = "--json" in sys.argv
    fail_on_dead = "--fail-on-dead" in sys.argv

    files = {module_name(p): p for p in APP.rglob("*.py")}
    imported = build_imports(files)

    def is_imported(mod: str) -> bool:
        return mod in imported or any(i.startswith(mod + ".") for i in imported)

    dead = []
    for mod in sorted(files):
        if mod in ENTRY or mod in DYNAMIC:
            continue
        if files[mod].name == "__init__.py":
            continue  # package marker, see module docstring
        if not is_imported(mod):
            lines = len(files[mod].read_text(encoding="utf-8").splitlines())
            dead.append({"module": mod, "path": str(files[mod].relative_to(BACKEND_ROOT)), "lines": lines})

    if as_json:
        print(json.dumps({"scanned": len(files), "dead": dead}, indent=2))
    else:
        print(f"scanned {len(files)} modules under app/")
        print(f"\n--- nothing imports these ({len(dead)}) ---")
        for d in dead:
            print(f"  {d['lines']:>5} lines  {d['module']}   ({d['path']})")
        if not dead:
            print("  (none)")
        total = sum(d["lines"] for d in dead)
        print(f"\nTOTAL: {len(dead)} module(s) / {total} lines")
        print("\nBefore deleting anything, grep the repo for the module's public names:")
        print("Python's dynamic edges (getattr, registry strings, importlib) are invisible here.")
        if fail_on_dead and dead:
            print(
                f"\nFAIL: {len(dead)} module(s) are not reachable from any import. "
                "Wire them up, delete them, or add them to DYNAMIC if loaded via importlib."
            )
            return 1
    return 1 if (fail_on_dead and dead) else 0


if __name__ == "__main__":
    raise SystemExit(main())
