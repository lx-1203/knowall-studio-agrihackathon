"""MCP (Model Context Protocol) Client and tools for agent integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.tools.base import Tool, ToolResult, tool_registry
from app.core.mcp.client import mcp_client, MCPClient

logger = logging.getLogger("knowall.mcp")


# ---------------------------------------------------------------------------
# MCP Tool Wrappers — built-in tools that extend the agent tool surface
# ---------------------------------------------------------------------------

class ExecPythonTool(Tool):
    """Execute Python code in a sandboxed environment."""

    @property
    def name(self) -> str:
        return "exec_python"

    @property
    def description(self) -> str:
        return "执行Python代码。可以进行数据计算、文件处理等操作。返回执行结果。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "要执行的Python代码",
                },
                "timeout": {
                    "type": "integer",
                    "description": "执行超时时间(秒)",
                    "default": 10,
                },
            },
            "required": ["code"],
        }

    # Modules allowed inside the sandbox. Imports of anything else are rejected.
    _SAFE_MODULES = {
        "math", "statistics", "random", "datetime", "json", "re",
        "collections", "itertools", "functools", "string", "textwrap",
        "unicodedata", "base64", "decimal", "fractions", "heapq", "bisect",
    }
    # Builtins NOT exposed — these are the RCE / file-escape primitives.
    _BLOCKED_BUILTIN_NAMES = {
        "open", "eval", "exec", "compile", "__import__", "input",
        "globals", "locals", "vars", "type", "getattr", "setattr",
        "delattr", "breakpoint", "memoryview", "help", "exit", "quit",
        "classmethod", "staticmethod", "property", "super", "object",
    }

    @staticmethod
    def _code_is_safe(code: str) -> str | None:
        """Return an error string if the code is unsafe, else None.

        Rejects dunder attribute access (``.__class__`` etc.), which is the
        classic sandbox escape, and any import of a module outside the allowlist.
        """
        import ast

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return f"代码语法错误: {e}"

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr.endswith("__"):
                return f"不允许访问 dunder 属性: {node.attr}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name.split(".")[0]
                    if mod not in ExecPythonTool._SAFE_MODULES:
                        return f"不允许导入模块: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[0]
                if node.level or mod not in ExecPythonTool._SAFE_MODULES:
                    return f"不允许从 {node.module or '(relative)'} 导入"
        return None

    async def execute(self, **kwargs) -> ToolResult:
        code = kwargs.get("code", "")
        timeout = kwargs.get("timeout", 10)

        if not code:
            return ToolResult(success=False, error="code is required")

        # Hard cap on timeout to avoid a runaway sandbox pinning a worker.
        timeout = max(1, min(int(timeout), 60))

        unsafe = self._code_is_safe(code)
        if unsafe:
            return ToolResult(success=False, error=f"Blocked: {unsafe}")

        # Build a builtins namespace WITHOUT the RCE primitives. Passing a dict
        # as __builtins__ makes Python use it directly, so open/eval/__import__
        # simply do not exist inside the sandbox.
        # __builtins__ is a module when imported and a dict in some contexts
        # (e.g. __main__ / -c), so read from either.
        import builtins as _pybuiltins
        if isinstance(__builtins__, dict):
            _all_builtins = __builtins__
        else:
            _all_builtins = vars(_pybuiltins)
        safe_builtins = {
            name: _all_builtins[name]
            for name in (
                "abs", "all", "any", "ascii", "bin", "bool", "bytearray",
                "bytes", "callable", "chr", "complex", "dict", "divmod",
                "enumerate", "filter", "float", "format", "frozenset", "hash",
                "hex", "int", "isinstance", "issubclass", "iter", "len", "list",
                "map", "max", "min", "next", "oct", "ord", "pow", "print",
                "range", "repr", "reversed", "round", "set", "slice", "sorted",
                "str", "sum", "tuple", "zip",
            )
            if name in _all_builtins
        }
        for name in self._BLOCKED_BUILTIN_NAMES:
            safe_builtins.pop(name, None)

        def _run() -> tuple[str, str]:
            import io
            import contextlib
            stdout = io.StringIO()
            stderr = io.StringIO()
            local_ns: dict[str, Any] = {}
            global_ns: dict[str, Any] = {"__builtins__": safe_builtins}
            try:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    exec(compile(code, "<agent>", "exec"), global_ns, local_ns)
            except BaseException as e:  # noqa: BLE001 - surface to caller
                stderr.write(f"{type(e).__name__}: {e}")
            return stdout.getvalue(), stderr.getvalue()

        try:
            # Run in a worker thread so the event loop is not blocked, and
            # enforce the timeout. A thread cannot be force-killed, but the
            # await returns control to the loop when the deadline passes.
            output, error = await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout)
            return ToolResult(
                success=not error,
                output=output if output else "(no output)",
                error=error,
            )
        except asyncio.TimeoutError:
            return ToolResult(success=False, error=f"执行超时（{timeout}s）")
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class CalculatorTool(Tool):
    """Mathematical expression evaluator."""

    @property
    def name(self) -> str:
        return "calculator"

    @property
    def description(self) -> str:
        return "计算数学表达式。支持基本运算、三角函数、对数等。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "数学表达式，如 2**10 + sin(3.14)",
                },
            },
            "required": ["expression"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        import math
        expr = kwargs.get("expression", "")
        if not expr:
            return ToolResult(success=False, error="expression is required")

        try:
            # Safe math evaluation
            allowed = {
                "abs": abs, "round": round, "min": min, "max": max,
                "sum": sum, "pow": pow, "int": int, "float": float,
                "sin": math.sin, "cos": math.cos, "tan": math.tan,
                "log": math.log, "log2": math.log2, "log10": math.log10,
                "sqrt": math.sqrt, "pi": math.pi, "e": math.e,
                "ceil": math.ceil, "floor": math.floor,
                "factorial": math.factorial, "gcd": math.gcd,
            }
            result = eval(expr, {"__builtins__": {}}, allowed)
            return ToolResult(success=True, output=str(result))
        except Exception as e:
            return ToolResult(success=False, error=str(e))


class FileReaderTool(Tool):
    """Read content from a file on the server."""

    @property
    def name(self) -> str:
        return "file_reader"

    @property
    def description(self) -> str:
        return "读取服务器上的文件内容。支持文本文件、Markdown等。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "最大读取字符数",
                    "default": 10000,
                },
            },
            "required": ["path"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        from pathlib import Path
        from app.config import settings

        path = kwargs.get("path", "")
        max_chars = kwargs.get("max_chars", 10000)

        if not path:
            return ToolResult(success=False, error="path is required")

        # Security: only allow reading within data directory
        try:
            file_path = Path(path).resolve()
            data_dir = Path(settings.document_dir).resolve()
            base_dir = Path(settings.chroma_persist_dir).parent.resolve()

            if not (str(file_path).startswith(str(data_dir)) or
                    str(file_path).startswith(str(base_dir))):
                return ToolResult(
                    success=False,
                    error="Access denied: file must be within data directory",
                )

            if not file_path.exists():
                return ToolResult(success=False, error=f"File not found: {path}")

            content = file_path.read_text(encoding="utf-8")[:max_chars]
            return ToolResult(
                success=True,
                output={"path": str(file_path), "content": content, "length": len(content)},
            )
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# Register built-in tools
tool_registry.register(ExecPythonTool())
tool_registry.register(CalculatorTool())
tool_registry.register(FileReaderTool())
