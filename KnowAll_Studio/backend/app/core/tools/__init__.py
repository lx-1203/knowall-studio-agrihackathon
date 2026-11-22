"""Agent tool framework — composable tools for AI agents.

Usage:
    from app.core.tools import tool_registry, execute_tool
    result = await execute_tool("web_search", query="Python tutorial")
"""
from app.core.tools.base import Tool, ToolRegistry, tool_registry, execute_tool
from app.core.tools import web_search, web_fetch, knowledge_search, paper_search

__all__ = [
    "Tool",
    "ToolRegistry",
    "tool_registry",
    "execute_tool",
]
