"""Base adapter for all LLM providers"""
from abc import ABC, abstractmethod
from typing import Any
from dataclasses import dataclass, field
import json


@dataclass
class AdapterConfig:
    temperature: float = 0.7
    max_tokens: int = 4096
    top_p: float = 1.0
    timeout: int = 120
    extra: dict = field(default_factory=dict)


@dataclass
class AdapterResponse:
    content: str
    raw_content: str
    model: str
    tokens_input: int = 0
    tokens_output: int = 0
    finish_reason: str = "stop"


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict


@dataclass
class ToolResponse:
    content: str
    raw_content: str
    model: str
    tool_calls: list[ToolCall]
    tokens_input: int = 0
    tokens_output: int = 0
    finish_reason: str = "stop"
    # Internal provider replay only; never serialize this field into a public response.
    assistant_message: dict | None = None


def parse_tool_arguments(value: str) -> dict:
    """Decode a JSON object without Python's non-JSON NaN/Infinity extension."""
    def invalid_constant(value):
        raise ValueError("Tool arguments must contain valid JSON values")
    if not isinstance(value, str):
        raise ValueError("Tool arguments must be a JSON string")
    arguments = json.loads(value, parse_constant=invalid_constant)
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object")
    # JSON numeric literals can overflow Python floats without invoking parse_constant.
    json.dumps(arguments, allow_nan=False)
    return arguments


def validate_tool_calls(calls: Any) -> list[ToolCall]:
    """Validate the canonical OpenAI function-call wire representation."""
    if not isinstance(calls, list):
        raise ValueError("Tool calls must be a list")
    result = []
    seen = set()
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function":
            raise ValueError("Only native function tool calls are supported")
        call_id = call.get("id")
        function = call.get("function")
        if not isinstance(call_id, str) or not call_id.strip() or call_id in seen:
            raise ValueError("Tool call IDs must be nonempty and unique")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"].strip():
            raise ValueError("Tool names must be nonempty strings")
        arguments = parse_tool_arguments(function.get("arguments"))
        result.append(ToolCall(call_id, function["name"], arguments))
        seen.add(call_id)
    return result


def validate_tool_schemas(tools: list[dict]) -> None:
    if not isinstance(tools, list):
        raise ValueError("Tools must be a list of function schemas")
    seen = set()
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            raise ValueError("Only function tool schemas are supported")
        function = tool.get("function")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"].strip():
            raise ValueError("Tool schema names must be nonempty strings")
        parameters = function.get("parameters")
        if not isinstance(parameters, dict) or parameters.get("type", "object") != "object":
            raise ValueError("Tool parameters must be an object schema")
        if function["name"] in seen:
            raise ValueError("Tool schema names must be unique")
        if "description" in function and not isinstance(function["description"], str):
            raise ValueError("Tool descriptions must be strings")
        seen.add(function["name"])


def validate_tool_history(messages: list[dict]) -> None:
    if not isinstance(messages, list):
        raise ValueError("Messages must be a list")
    pending = set()
    seen = set()
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("Each message must be an object")
        if "content" not in message:
            raise ValueError("Every history message must have explicit content")
        role = message.get("role")
        if role not in ("system", "user", "assistant", "tool") or message.get("function_call") is not None:
            raise ValueError("Unsupported tool history message")
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending or not isinstance(message.get("content"), str):
                raise ValueError("Tool results must answer pending calls with string content")
            pending.remove(call_id)
            continue
        if pending:
            raise ValueError("All tool calls must be answered before another turn")
        calls = validate_tool_calls(message.get("tool_calls", [])) if role == "assistant" else []
        if role != "assistant" and message.get("tool_calls") not in (None, []):
            raise ValueError("Only assistant messages may call tools")
        if not isinstance(message.get("content"), str) and not (role == "assistant" and calls and message.get("content") is None):
            raise ValueError("Message content must be a string or a tool-bearing assistant null")
        for call in calls:
            if call.call_id in seen:
                raise ValueError("Tool call IDs must be unique across history")
            pending.add(call.call_id)
            seen.add(call.call_id)
    if pending:
        raise ValueError("Tool history contains unanswered calls")


class InvalidResponseError(ValueError):
    """Unusable provider payload with validated usage, if available."""

    def __init__(self, message: str, tokens_input: int = 0, tokens_output: int = 0):
        super().__init__(message)
        self.tokens_input = tokens_input
        self.tokens_output = tokens_output


class BaseModelAdapter(ABC):
    """Abstract base for all model adapters"""

    provider: str = "base"
    base_url: str = ""

    def __init__(self, api_key: str, base_url: str | None = None):
        self.api_key = api_key
        if base_url:
            self.base_url = base_url

    @abstractmethod
    async def chat_completion(
        self, messages: list[dict[str, str]], config: AdapterConfig
    ) -> AdapterResponse:
        """Call model API and return standardized response"""
        ...

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Estimate token count for text"""
        ...

    async def chat_completion_with_tools(
        self, messages: list[dict], tools: list[dict], config: AdapterConfig
    ) -> ToolResponse:
        """Native non-streaming function protocol; unsupported providers fail explicitly."""
        raise NotImplementedError(f"{self.provider} does not support native tool completion")

    def validate_response(self, raw: Any) -> AdapterResponse:
        """Validate and normalize API response. Override in subclasses for provider-specific logic."""
        return raw if isinstance(raw, AdapterResponse) else AdapterResponse(
            content=str(raw),
            raw_content=str(raw),
            model="unknown",
        )

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _normalize_messages(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """Normalize messages to OpenAI chat format"""
        return messages
