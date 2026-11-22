#!/usr/bin/env python
"""Find N+1 query candidates: a database round-trip *inside* a loop.

Why this exists
---------------
Rounds 4-15 of the optimisation work fixed a series of N+1s (22 sites across
15 files). The detector used at the time was an ad-hoc script that was never
committed, so the claim "there are no real N+1s left" could not be re-checked by
anyone — including a future run of this session. A measurement you cannot
re-run is not evidence.

This turns that detector into a repeatable gate with a curated allowlist.

What counts as a candidate
--------------------------
An `await <x>.execute|get|scalar|scalars|scalar_one|scalar_one_or_none|refresh|
delete(...)` appearing in the body of a `for`/`while`/async-for loop, where the
loop is not itself inside a nested function definition (a helper defined inside
a loop is not executed per iteration).

What this deliberately does NOT flag
------------------------------------
* Writes inside a loop (`session.add()` then one `commit()`) — not a read N+1 and
  usually the correct pattern.
* Network calls (`httpx`, the LLM client) inside loops. Those can be just as
  bad, but conflating them with SQL round-trips makes both signals harder to
  read. Tracked separately in the backlog.
* `.all()` / `.first()` on an already-materialised result — no round-trip.

Python is dynamic, so results are *candidates*. Anything genuinely fine must be
listed in ALLOWED with a reason; the gate then stays meaningful instead of being
progressively ignored.

Usage
-----
    python scripts/find_n_plus_one.py [--json] [--fail-on-findings]

Run from anywhere; paths are resolved relative to this file.
"""
from __future__ import annotations

import ast
import json
import pathlib
import sys

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = BACKEND_ROOT / "app"

# Methods that issue a database round-trip.
#
# `delete` is deliberately absent: `AsyncSession.delete(obj)` only *marks* the
# object; the actual DELETE is emitted by the flush at commit, batched. A
# per-row `await db.delete(row)` is therefore not an N+1 — it is a style
# question (a bulk `delete(Model).where(...)` is tidier) tracked in the backlog.
DB_METHODS = {
    "execute",
    "get",
    "scalar",
    "scalars",
    "scalar_one",
    "scalar_one_or_none",
    "refresh",
}

# `get`/`refresh` are also everyday names on queues, caches and dicts
# (`asyncio.Queue.get()` is the classic false positive). Two signals distinguish
# a database handle:
#
#   1. the receiver name looks like a session (`db`, `session`, `self.session`),
#      matched per dotted component so `caches` cannot masquerade as `s`;
#   2. `Session.get()` takes `(entity, ident)` — two positional args — whereas
#      `queue.get()` / `cache.get(key)` take none or one. This catches sessions
#      with short or unusual names that no naming convention would find.
AMBIGUOUS_METHODS = {"get", "refresh"}
SESSION_HINTS = {"session", "db", "sess", "conn", "tx"}


def _looks_like_session(node: ast.AST) -> bool:
    """True when the receiver's dotted name has a session-ish component."""
    name = _receiver_name(node).lower()
    return any(
        part in SESSION_HINTS or "session" in part or "conn" in part
        for part in name.split(".")
        if part
    )


def _get_takes_entity_and_pk(call: ast.Call) -> bool:
    """`Session.get(Entity, pk)` — at least two positional args."""
    return len([a for a in call.args if not isinstance(a, ast.Starred)]) >= 2


def _receiver_name(node: ast.AST) -> str:
    """Best-effort dotted name of the call receiver, e.g. `self.session`."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))

# Reviewed candidates that are NOT N+1s, keyed by "relative/path.py::qualname".
# Every entry needs a reason. Keep this list short — it is a record of judgement,
# not a place to silence the tool.
ALLOWED: dict[str, str] = {
    "app/api/commercial.py::delete_my_data": (
        "Loops over a fixed list of 12 tables (schema-bounded, not data-bounded). "
        "Row deletes are batched by the unit of work: "
        "tests/test_commercial/test_delete_my_data_queries.py asserts the statement "
        "count is identical for 2 rows/table and 40 rows/table."
    ),
    "app/api/live_session.py::create_session": (
        "Room-code collision retry, `for _ in range(10)`, and it breaks on the "
        "first free code — normally one iteration. Bounded constant, not data-driven."
    ),
    "app/websocket/sync_server.py::sync_websocket": (
        "Inside the `while True:` websocket message loop: one query per inbound "
        "message is the request/response pattern, not an N+1. The loop is an event "
        "loop, so its trip count is not a function of any query's row count."
    ),
}


# Names whose body issues a database call. Used to catch the blind spot that
# let a real N+1 hide: calling self._get_accuracy(node.id, session) inside a loop
# looks nothing like a query, yet _get_accuracy runs two of them per call.
# CoverageEngine.calculate() was therefore 2 + N SELECTs (measured: 2 nodes -> 5,
# 30 nodes -> 33) while this tool reported the file as clean.
#
# Only *names* are tracked, so a same-named unrelated function elsewhere could in
# principle be flagged — a false positive the reviewer resolves, which is the
# correct direction to err for a gate like this.
# Deliberately narrower than the set used for *direct* loop-body calls.
# .count() is a list/str method far more often than a SQL aggregate, and an
# unqualified .get() is usually a dict. Including them marked every LLM helper
# (generate, generate_questions, ...) as querying internally and produced 26
# candidates where 3 were real - a gate that cries wolf gets ignored, which is
# worse than no gate.
# Only helpers that *read* are tracked. A helper that merely writes (usage
# logging, audit rows) being called in a loop is usually intentional, and
# including those marked every LLM wrapper as suspect: 26 candidates for 1 real
# finding. Reads are what an N+1 is about, and they are detectable — a result is
# only materialised by fetchall/fetchone/scalars/scalar_one, and the statement is
# built by select().
_BODY_READ_ATTRS = {"fetchall", "fetchone", "scalars", "scalar", "scalar_one",
                    "scalar_one_or_none"}


def _body_looks_like_read(call: ast.Call) -> bool:
    func = call.func
    # select(...) itself — the statement is assembled here when the helper
    # builds its own query rather than receiving one.
    if isinstance(func, ast.Name) and func.id == "select":
        return True
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in _BODY_READ_ATTRS:
        return True
    if func.attr in AMBIGUOUS_METHODS:
        return _looks_like_session(func.value) or _get_takes_entity_and_pk(call)
    return False


def collect_querying_callables(trees: list[ast.AST]) -> set[str]:
    """Names of functions/methods whose own body performs a database call."""
    names: set[str] = set()
    for tree in trees:
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(fn):
                if isinstance(call, ast.Call) and _body_looks_like_read(call):
                    names.add(fn.name)
                    break
    return names


class _LoopDbVisitor(ast.NodeVisitor):
    def __init__(self, relpath: str, querying: set[str] | None = None) -> None:
        self.relpath = relpath
        self.querying = querying or set()
        self.qualname: list[str] = []
        self.loop_depth = 0
        self.findings: list[dict] = []

    # -- scope tracking ---------------------------------------------------
    def _visit_func(self, node: ast.AST) -> None:
        name = getattr(node, "name", "<lambda>")
        self.qualname.append(name)
        outer_loop_depth = self.loop_depth
        # A function defined inside a loop is not executed once per iteration
        # unless someone calls it there; treating it as loop-body would be noise.
        self.loop_depth = 0
        self.generic_visit(node)
        self.loop_depth = outer_loop_depth
        self.qualname.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    # -- loop tracking ----------------------------------------------------
    def _visit_loop(self, node: ast.AST) -> None:
        # `iter` / `target` / `test` are evaluated **once** to drive the loop, so
        # `for row in (await db.execute(stmt)).all():` is a single round-trip and
        # must not be counted as a loop body. Visiting them at the outer depth is
        # what keeps this tool from crying wolf on the batched pattern it is
        # supposed to be protecting.
        for field in ("iter", "target", "test"):
            child = getattr(node, field, None)
            if child is not None:
                self.visit(child)

        self.loop_depth += 1
        for field in ("body", "orelse"):
            for stmt in getattr(node, field, None) or []:
                self.visit(stmt)
        self.loop_depth -= 1

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(node)

    # -- comprehensions ---------------------------------------------------
    def _visit_comp(self, node: ast.AST, body_fields: tuple[str, ...]) -> None:
        # `for row in (await db.execute(q)).all()` inside a comprehension: the
        # first `iter` runs once, everything else runs per element. An `await`
        # in the element expression is a genuine N+1 and is easy to write by
        # accident, so it is worth catching.
        generators = getattr(node, "generators", [])
        if generators:
            self.visit(generators[0].iter)
        self.loop_depth += 1
        for gen in generators:
            for cond in gen.ifs:
                self.visit(cond)
        for field in body_fields:
            child = getattr(node, field, None)
            if child is not None:
                self.visit(child)
        self.loop_depth -= 1

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comp(node, ("elt",))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comp(node, ("elt",))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comp(node, ("elt",))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comp(node, ("key", "value"))

    def _record(self, node: ast.Await, method: str, via: str = "") -> None:
        self.findings.append(
            {
                "path": self.relpath,
                "line": node.lineno,
                "qualname": ".".join(self.qualname) or "<module>",
                "method": method,
                "via": via,
            }
        )

    def _helper_name(self, call: ast.Call) -> str | None:
        """Name of the callee when it is a function known to query internally."""
        func = call.func
        if isinstance(func, ast.Attribute) and func.attr in self.querying:
            return func.attr
        if isinstance(func, ast.Name) and func.id in self.querying:
            return func.id
        return None

    # -- the actual detection ---------------------------------------------
    def visit_Await(self, node: ast.Await) -> None:
        if self.loop_depth > 0:
            call = node.value
            if isinstance(call, ast.Call):
                # (a) a direct database round-trip
                if isinstance(call.func, ast.Attribute) and call.func.attr in DB_METHODS:
                    method = call.func.attr
                    if method in AMBIGUOUS_METHODS and not (
                        _looks_like_session(call.func.value)
                        or _get_takes_entity_and_pk(call)
                    ):
                        # e.g. queue.get() — not a database round-trip.
                        self.generic_visit(node)
                        return
                    self._record(node, method)
                    self.generic_visit(node)
                    return
                # (b) a call to a helper that queries internally — the shape that
                # hid a real N+1 from this tool until round 24.
                helper = self._helper_name(call)
                if helper:
                    self._record(node, helper, via="helper")
        self.generic_visit(node)


def scan() -> list[dict]:
    trees: list[tuple[str, ast.AST]] = []
    findings: list[dict] = []
    for py in sorted(APP.rglob("*.py")):
        if "__pycache__" in str(py):
            continue
        rel = py.relative_to(BACKEND_ROOT).as_posix()
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover - syntax is checked in CI
            findings.append({"path": rel, "line": exc.lineno or 0, "qualname": "<syntax error>", "method": "?"})
            continue
        trees.append((rel, tree))

    # Pass 1: which functions/methods query internally? Needed before pass 2 can
    # recognise a loop that calls one of them.
    querying = collect_querying_callables([t for _, t in trees])

    # Pass 2: find database work inside loops, direct or via such a helper.
    for rel, tree in trees:
        visitor = _LoopDbVisitor(rel, querying)
        visitor.visit(tree)
        findings.extend(visitor.findings)
    return findings


def main() -> int:
    as_json = "--json" in sys.argv
    fail_on_findings = "--fail-on-findings" in sys.argv

    findings = scan()

    # Only *direct* database calls in a loop gate the build. A call to a helper
    # that happens to read is a strong hint to go look, but name-based transitive
    # analysis cannot separate "reads per item" from "reads config once per LLM
    # call" — it flagged 14 real-looking sites of which 1 was an actual N+1.
    # Splitting them keeps the gate precise (so it is not ignored) while still
    # surfacing the blind spot that hid CoverageEngine.calculate() until round 24.
    direct = [f for f in findings if not f.get("via")]
    mediated = [f for f in findings if f.get("via")]
    unreviewed = [f for f in direct if f"{f['path']}::{f['qualname']}" not in ALLOWED]

    if as_json:
        print(json.dumps({"direct": direct, "mediated_by_helper": mediated,
                          "unreviewed": unreviewed}, indent=2))
    else:
        print(f"scanned {APP} -> {len(direct)} direct + {len(mediated)} helper-mediated candidate(s)")
        for f in direct:
            key = f"{f['path']}::{f['qualname']}"
            mark = "ALLOWED" if key in ALLOWED else "UNREVIEWED"
            print(f"  [{mark}] {f['path']}:{f['line']}  {f['qualname']}()  .{f['method']}()")

        if mediated:
            print()
            print(f"--- {len(mediated)} call(s) to a helper that reads (informational, NOT gated) ---")
            print("    Each is a place to check by hand; most are legitimate.")
            for f in mediated:
                print(f"    {f['path']}:{f['line']}  {f['qualname']}() -> .{f['method']}()")

        if unreviewed:
            print()
            print(f"{len(unreviewed)} DIRECT candidate(s) need review.")
            print("Verify each one, then either fix it or add it to ALLOWED with a reason.")
            print("Do NOT add to ALLOWED just to make CI green.")
        else:
            print()
            print("All direct candidates reviewed (see ALLOWED in this file).")

    if fail_on_findings and unreviewed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
