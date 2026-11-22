"""Anthropic Messages API adapter"""
import json
import asyncio
from copy import deepcopy
import tiktoken
from .base import (BaseModelAdapter, AdapterConfig, AdapterResponse, InvalidResponseError,
                   ToolResponse, validate_tool_calls, validate_tool_schemas, validate_tool_history)
from .http_utils import sync_post


class AnthropicAdapter(BaseModelAdapter):
    """Adapter for Anthropic Messages API (and compatible endpoints like DeepSeek)."""

    provider = "anthropic"

    def _build_headers(self):
        return {**super()._build_headers(), 'x-api-key': self.api_key}

    def __init__(self, api_key: str, base_url: str = "https://api.anthropic.com/v1", model_name: str = "claude-sonnet-4-6"):
        super().__init__(api_key, base_url)
        self.model_name = model_name
        self._api_version = "2023-06-01"
        try:
            self._encoder = tiktoken.encoding_for_model("gpt-4o")
        except Exception:
            self._encoder = tiktoken.get_encoding("cl100k_base")

    async def chat_completion(
        self, messages: list[dict[str, str]], config: AdapterConfig
    ) -> AdapterResponse:
        url = f"{self.base_url.rstrip('/')}/messages"
        headers = self._build_headers()
        headers.update({
            "anthropic-version": self._api_version,
            "Content-Type": "application/json",
        })

        # Convert OpenAI-format messages to Anthropic format
        system_prompt = ""
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_prompt = msg["content"]
            else:
                content = msg["content"]
                # Handle multimodal content (list of content blocks)
                if isinstance(content, list):
                    anthropic_content = self._convert_multimodal_content(content)
                else:
                    anthropic_content = content
                anthropic_messages.append({
                    "role": msg["role"],
                    "content": anthropic_content,
                })

        payload = {
            "model": self.model_name,
            "messages": anthropic_messages,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "top_p": config.top_p,
        }
        if system_prompt:
            payload["system"] = system_prompt
        payload.update(config.extra)

        data = await asyncio.to_thread(sync_post, url, headers, payload, config.timeout)
        return self._parse_response(data)

    def _convert_multimodal_content(self, content_blocks: list[dict]) -> list[dict]:
        """Convert OpenAI-format multimodal content blocks to Anthropic format."""
        anthropic_blocks = []
        for block in content_blocks:
            if block.get("type") == "text":
                anthropic_blocks.append({"type": "text", "text": block["text"]})
            elif block.get("type") == "image_url":
                image_url = block.get("image_url", {})
                url = image_url.get("url", "")
                # Parse data URL: data:image/png;base64,<data>
                if url.startswith("data:"):
                    import re
                    import base64
                    match = re.match(r"data:(image/\w+);base64,(.+)", url)
                    if match:
                        media_type = match.group(1)
                        img_data = match.group(2)
                        # Validate base64
                        try:
                            base64.b64decode(img_data)
                        except Exception:
                            continue
                        anthropic_blocks.append({
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": img_data,
                            },
                        })
            # Skip unknown block types
        return anthropic_blocks

    def count_tokens(self, text: str) -> int:
        return len(self._encoder.encode(text, disallowed_special=()))

    async def chat_completion_with_tools(
        self, messages: list[dict], tools: list[dict], config: AdapterConfig
    ) -> ToolResponse:
        validate_tool_schemas(tools)
        validate_tool_history(messages)
        system = []
        wire_messages = []
        for message in messages:
            role = message["role"]
            if role == "system":
                system.append(message["content"])
                continue
            if role == "tool":
                block = {"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": message["content"]}
                if wire_messages and wire_messages[-1]["role"] == "user" and isinstance(wire_messages[-1]["content"], list):
                    wire_messages[-1]["content"].append(block)
                else:
                    wire_messages.append({"role": "user", "content": [block]})
                continue
            calls = validate_tool_calls(message.get("tool_calls", [])) if role == "assistant" else []
            if role == "assistant" and "_anthropic_content" in message:
                # Runtime-only roundtrip metadata. Validate it against canonical fields
                # before replaying signed thinking blocks, without displaying reasoning.
                blocks = message["_anthropic_content"]
                replay = self._parse_tool_response({"content": blocks, "stop_reason": "tool_use" if calls else "end_turn"})
                if replay.content != (message["content"] or "") or replay.tool_calls != calls:
                    raise ValueError("Anthropic replay does not match canonical history")
                content = deepcopy(blocks)
            elif calls:
                content = []
                if message["content"]:
                    content.append({"type": "text", "text": message["content"]})
                content.extend({"type": "tool_use", "id": call.call_id, "name": call.name, "input": call.arguments} for call in calls)
            else:
                content = message["content"]
            # The provider expects alternating roles; consecutive tool results share
            # one user message, and following user text remains after those results.
            if wire_messages and wire_messages[-1]["role"] == role:
                previous = wire_messages[-1]["content"]
                if isinstance(previous, str):
                    previous = [{"type": "text", "text": previous}]
                    wire_messages[-1]["content"] = previous
                previous.extend(content if isinstance(content, list) else [{"type": "text", "text": content}])
            else:
                wire_messages.append({"role": role, "content": content})
        native_tools = []
        for tool in tools:
            function = tool["function"]
            native = {"name": function["name"], "input_schema": function["parameters"]}
            if "description" in function:
                native["description"] = function["description"]
            native_tools.append(native)
        payload = {key: value for key, value in config.extra.items() if key not in
                   ("functions", "function_call", "system", "n")}
        payload.update({"model": self.model_name, "messages": wire_messages, "tools": native_tools,
                        "max_tokens": config.max_tokens, "temperature": config.temperature,
                        "top_p": config.top_p, "tool_choice": {"type": "auto"}, "stream": False})
        if system:
            payload["system"] = "\n\n".join(system)
        headers = self._build_headers()
        headers.update({"anthropic-version": self._api_version, "Content-Type": "application/json"})
        data = await asyncio.to_thread(sync_post, f"{self.base_url.rstrip('/')}/messages", headers, payload, config.timeout)
        return self._parse_tool_response(data)

    def _parse_tool_response(self, data: dict) -> ToolResponse:
        if not isinstance(data, dict):
            raise InvalidResponseError("Provider response must be an object")
        usage = data.get("usage")
        if usage is None:
            usage = {}
        if not isinstance(usage, dict):
            raise InvalidResponseError("Provider usage must be an object")
        counts = [usage.get("input_tokens", 0), usage.get("output_tokens", 0)]
        if any(type(value) is not int or value < 0 for value in counts):
            raise InvalidResponseError("Provider usage must contain nonnegative token counts")
        try:
            blocks = data.get("content")
            if "error" in data or not isinstance(blocks, list) or data.get("function_call") is not None or data.get("refusal") is not None:
                raise ValueError("Provider returned an error or invalid native content")
            text = []
            canonical_calls = []
            for block in blocks:
                if not isinstance(block, dict):
                    raise ValueError("Provider returned an invalid content block")
                kind = block.get("type")
                if kind == "text":
                    if not isinstance(block.get("text"), str):
                        raise ValueError("Provider returned invalid text")
                    text.append(block["text"])
                elif kind == "tool_use":
                    arguments = block.get("input")
                    if not isinstance(arguments, dict):
                        raise ValueError("Tool arguments must be a JSON object")
                    canonical_calls.append({"id": block.get("id"), "type": "function", "function": {
                        "name": block.get("name"), "arguments": json.dumps(arguments, ensure_ascii=False, allow_nan=False)}})
                elif kind == "thinking":
                    if not isinstance(block.get("thinking"), str) or not isinstance(block.get("signature"), str) or not block["signature"]:
                        raise ValueError("Provider returned unsigned or invalid thinking")
                elif kind == "redacted_thinking":
                    if not isinstance(block.get("data"), str) or not block["data"]:
                        raise ValueError("Provider returned invalid redacted thinking")
                else:
                    raise ValueError("Provider returned an unsupported native block")
            calls = validate_tool_calls(canonical_calls)
            finish_reason = data.get("stop_reason")
            if finish_reason != ("tool_use" if calls else "end_turn"):
                raise ValueError("Provider returned an incomplete or inconsistent stop reason")
            if not calls and not text:
                raise ValueError("Provider returned no text or tool calls")
            model = data.get("model", self.model_name)
            if not isinstance(model, str) or not model.strip():
                raise ValueError("Provider returned an invalid model")
        except (ValueError, TypeError) as error:
            raise InvalidResponseError(str(error), *counts) from error
        content = "".join(text)
        assistant_message = {"role": "assistant", "content": content or (None if calls else ""),
                             "_anthropic_content": blocks}
        if calls:
            assistant_message["tool_calls"] = canonical_calls
        return ToolResponse(content, json.dumps(data, ensure_ascii=False), model, calls,
                            *counts, finish_reason, assistant_message)

    def _parse_response(self, data: dict) -> AdapterResponse:
        if not isinstance(data, dict):
            raise InvalidResponseError("Provider response must be an object")
        usage = data.get("usage")
        if usage is None:
            usage = {}
        if not isinstance(usage, dict):
            raise InvalidResponseError("Provider usage must be an object")
        counts = [usage.get("input_tokens", 0), usage.get("output_tokens", 0)]
        if any(type(value) is not int or value < 0 for value in counts):
            raise InvalidResponseError("Provider usage must contain nonnegative token counts")
        content_blocks = data.get("content")
        if "error" in data or not isinstance(content_blocks, list):
            raise InvalidResponseError("Provider returned an error or invalid content blocks", *counts)
        parts = []
        for block in content_blocks:
            if not isinstance(block, dict):
                raise InvalidResponseError("Provider returned an invalid content block", *counts)
            if block.get("type") == "text":
                if not isinstance(block.get("text"), str):
                    raise InvalidResponseError("Provider returned invalid text", *counts)
                parts.append(block["text"])
            elif block.get("type") not in ("thinking", "redacted_thinking"):
                raise InvalidResponseError("Model tools or unknown content blocks are not supported", *counts)
        text = "".join(parts)
        return AdapterResponse(
            content=text,
            raw_content=json.dumps(data, ensure_ascii=False),
            model=data.get("model", self.model_name),
            tokens_input=counts[0],
            tokens_output=counts[1],
            finish_reason=data.get("stop_reason", "stop"),
        )
