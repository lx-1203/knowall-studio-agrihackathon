"""Base tool class and registry for the agent tool system."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("knowall.tools")


@dataclass
class ToolResult:
    """Result from a tool execution."""
    success: bool
    output: Any = None
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class Tool(ABC):
    """Base class for all agent tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool identifier."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description for the LLM."""
        ...

    @property
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for the tool's parameters."""
        return {"type": "object", "properties": {}, "required": []}

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with the given parameters."""
        ...

    def to_openai_tool(self) -> dict:
        """Export as an OpenAI-compatible tool definition."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Registry for managing agent tools."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool):
        """Register a tool instance."""
        self._tools[tool.name] = tool
        logger.debug("Tool registered: %s", tool.name)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[dict]:
        """List all registered tools with their metadata."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in self._tools.values()
        ]

    def get_openai_tools(self, names: list[str] | None = None) -> list[dict]:
        """Export tools as OpenAI-compatible tool definitions."""
        tools = self._tools.values()
        if names:
            tools = [t for t in tools if t.name in names]
        return [t.to_openai_tool() for t in tools]

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# Global registry singleton
tool_registry = ToolRegistry()


async def execute_tool(name: str, **kwargs) -> ToolResult:
    """Execute a tool by name."""
    tool = tool_registry.get(name)
    if tool is None:
        return ToolResult(success=False, error=f"Unknown tool: {name}")
    try:
        return await tool.execute(**kwargs)
    except Exception as e:
        logger.error("Tool %s failed: %s", name, e)
        return ToolResult(success=False, error=str(e))
