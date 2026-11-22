"""Fail CI when a test skip/xfail is added without an explicit review.

Run from any directory: python KnowAll_Studio/backend/scripts/check_test_skips.py
The allowlist records a reason for Python markers and the full argument list for
Playwright markers, so changing a conditional skip also requires review.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
from typing import Mapping


REPO_ROOT = Path(__file__).resolve().parents[3]
APPROVED: dict[str, str] = {
    "backend/tests/test_chunking_splitter.py::test_extract_image_refs": (
        "ExtractImageRefs 依赖 WeKnora 内部包 docparser.UnwrapLinkedImages（跨包依赖），"
        "且不在本次约定的公开 API 内，未移植 —— 对应的 TestExtractImageRefs 一并跳过。"
    ),
    "backend/tests/test_docreader_markdown_parser.py::test_normalize_markitdown_en_tables": (
        "上游 fixtures/en_tables.docx 未随仓库提供"
    ),
    "frontend/tests/e2e/12-glossary.spec.ts::test.skip": (
        "!hasBackend, '需要后端 + 词库数据（设置 GLOSSARY_E2E=1 运行）'"
    ),
}

PYTHON_MARKERS = {
    "pytest.mark.skip",
    "pytest.mark.skipif",
    "pytest.mark.xfail",
    "pytest.skip",
    "pytest.xfail",
    "pytest.importorskip",
    "unittest.skip",
    "unittest.skipIf",
    "unittest.skipUnless",
}
TS_MARKER = re.compile(r"\b(?:test|it|describe)(?:\.describe)?\.(?:skip|fixme|fail)\s*\(")


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    return ""


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for name in node.names:
                if name.name in {"pytest", "unittest"} and name.asname:
                    aliases[name.asname] = name.name
        elif isinstance(node, ast.ImportFrom) and node.module in {"pytest", "unittest"}:
            for name in node.names:
                if name.asname:
                    aliases[name.asname] = f"{node.module}.{name.name}"
    return aliases


def _marker_name(node: ast.AST, aliases: Mapping[str, str]) -> str:
    name = _name(node)
    head, separator, tail = name.partition(".")
    return aliases.get(head, head) + separator + tail


def _python_reason(call: ast.Call, marker: str) -> str | None:
    reason = next((kw.value for kw in call.keywords if kw.arg == "reason"), None)
    if reason is None:
        position = 1 if marker in {"pytest.mark.skipif", "unittest.skipIf", "unittest.skipUnless", "pytest.importorskip"} else 0
        if len(call.args) > position:
            reason = call.args[position]
    return reason.value if isinstance(reason, ast.Constant) and isinstance(reason.value, str) else None


def _ts_arguments(source: str, opening: int) -> str | None:
    depth = 1
    quote = ""
    escaped = False
    for index in range(opening + 1, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
        elif char in "'\"`":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return source[opening + 1:index]
    return None


def _ts_code_only(source: str) -> str:
    """Mask comments and strings while preserving positions for diagnostics."""
    masked = list(source)
    state = "code"
    escaped = False
    index = 0
    while index < len(source):
        char = source[index]
        next_char = source[index + 1] if index + 1 < len(source) else ""
        if state == "code":
            if char == "/" and next_char in {"/", "*"}:
                state = "line" if next_char == "/" else "block"
                masked[index:index + 2] = [" ", " "]
                index += 2
                continue
            if char in "'\"`":
                state = char
                masked[index] = " "
        elif state == "line":
            if char == "\n":
                state = "code"
            else:
                masked[index] = " "
        elif state == "block":
            masked[index] = "\n" if char == "\n" else " "
            if char == "*" and next_char == "/":
                masked[index + 1] = " "
                index += 2
                state = "code"
                continue
        else:
            masked[index] = "\n" if char == "\n" else " "
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == state:
                state = "code"
        index += 1
    return "".join(masked)


def check_skips(root: Path, approved: Mapping[str, str] = APPROVED) -> list[str]:
    """Return actionable diagnostics for unapproved or changed test skips."""
    issues: list[str] = []
    seen: set[str] = set()
    backend = root / "backend/tests"
    for path in sorted(backend.rglob("test_*.py")) if backend.exists() else []:
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            issues.append(f"{relative}:{exc.lineno}: syntax error: {exc.msg}")
            continue
        aliases = _import_aliases(tree)
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Attribute, ast.Name)) and _marker_name(node, aliases) in PYTHON_MARKERS:
                parent = parents.get(node)
                if not (isinstance(parent, ast.Call) and parent.func is node):
                    issues.append(f"{relative}:{node.lineno}: bare {_marker_name(node, aliases)} is not approved (no reason)")
            if not isinstance(node, ast.Call):
                continue
            marker = _marker_name(node.func, aliases)
            if marker not in PYTHON_MARKERS:
                continue
            owner = node
            while owner in parents and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = parents[owner]
            function = owner.name if isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)) else "<module>"
            key = f"{relative}::{function}"
            reason = _python_reason(node, marker)
            if key in seen:
                issues.append(f"{relative}:{node.lineno}: duplicate skip marker for {function}")
            seen.add(key)
            if key not in approved:
                issues.append(f"{relative}:{node.lineno}: {marker} is not approved ({function})")
            elif not reason or reason != approved[key]:
                issues.append(f"{relative}:{node.lineno}: approved skip reason changed or missing ({function})")

    frontend = root / "frontend/tests/e2e"
    for path in sorted(frontend.rglob("*.ts")) if frontend.exists() else []:
        relative = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        for match in TS_MARKER.finditer(_ts_code_only(source)):
            marker = match.group().split("(", 1)[0].strip()
            line = source.count("\n", 0, match.start()) + 1
            key = f"{relative}::{marker}"
            arguments = _ts_arguments(source, match.end() - 1)
            if key in seen:
                issues.append(f"{relative}:{line}: duplicate skip marker for {marker}")
            seen.add(key)
            if key not in approved:
                issues.append(f"{relative}:{line}: {marker} is not approved")
            elif arguments is None or " ".join(arguments.split()) != " ".join(approved[key].split()):
                issues.append(f"{relative}:{line}: approved skip reason or condition changed")
    return issues


if __name__ == "__main__":
    findings = check_skips(REPO_ROOT / "KnowAll_Studio")
    if findings:
        print("\n".join(findings))
        raise SystemExit(1)
    print(f"PASS: test skip allowlist ({len(APPROVED)} reviewed markers)")
