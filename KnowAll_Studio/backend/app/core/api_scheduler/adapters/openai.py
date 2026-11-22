"""OpenAI-compatible adapter (works for OpenAI, DeepSeek, Ollama, vLLM, etc.)"""
import json
import asyncio
import tiktoken
from .base import (BaseModelAdapter, AdapterConfig, AdapterResponse, InvalidResponseError,
                   ToolResponse, validate_tool_calls, validate_tool_schemas, validate_tool_history)
from .http_utils import sync_post


class OpenAICompatAdapter(BaseModelAdapter):
    """Adapter for any OpenAI-compatible API (OpenAI, DeepSeek, Ollama, vLLM, etc.)"""

    provider = "openai_compat"

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1", model_name: str = "gpt-4o"):
        super().__init__(api_key, base_url)
        self.model_name = model_name
        try:
            self._encoder = tiktoken.encoding_for_model("gpt-4o")
        except Exception:
            self._encoder = tiktoken.get_encoding("cl100k_base")

    async def chat_completion(
        self, messages: list[dict[str, str]], config: AdapterConfig
    ) -> AdapterResponse:
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = self._build_headers()
        headers["User-Agent"] = "KnowAll-Studio/1.0"
        headers["Accept"] = "application/json"
        payload = {
            "model": self.model_name,
            "messages": self._normalize_messages(messages),
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "top_p": config.top_p,
        }
        payload.update(config.extra)

        data = await asyncio.to_thread(sync_post, url, headers, payload, config.timeout)
        return self._parse_response(data)

    def count_tokens(self, text: str) -> int:
        return len(self._encoder.encode(text, disallowed_special=()))

    async def chat_completion_with_tools(
        self, messages: list[dict], tools: list[dict], config: AdapterConfig
    ) -> ToolResponse:
        validate_tool_schemas(tools)
        validate_tool_history(messages)
        # Private provider replay and legacy controls never reach an OpenAI endpoint.
        wire_messages = [{key: value for key, value in message.items() if key in
                          ("role", "content", "tool_calls", "tool_call_id")} for message in messages]
        payload = {key: value for key, value in config.extra.items() if key not in ("functions", "function_call")}
        payload.update({"model": self.model_name, "messages": wire_messages, "tools": tools,
                        "temperature": config.temperature, "max_tokens": config.max_tokens,
                        "top_p": config.top_p, "tool_choice": "auto", "stream": False, "n": 1})
        headers = self._build_headers()
        headers.update({"User-Agent": "KnowAll-Studio/1.0", "Accept": "application/json"})
        data = await asyncio.to_thread(sync_post, f"{self.base_url.rstrip('/')}/chat/completions",
                                       headers, payload, config.timeout)
        return self._parse_tool_response(data)

    def _parse_tool_response(self, data: dict) -> ToolResponse:
        if not isinstance(data, dict):
            raise InvalidResponseError("Provider response must be an object")
        usage = data.get("usage")
        if usage is None:
            usage = {}
        if not isinstance(usage, dict):
            raise InvalidResponseError("Provider usage must be an object")
        counts = [usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)]
        if any(type(value) is not int or value < 0 for value in counts):
            raise InvalidResponseError("Provider usage must contain nonnegative token counts")
        try:
            choices = data.get("choices")
            if "error" in data or not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
                raise ValueError("Provider returned an error or invalid choices")
            choice = choices[0]
            message = choice.get("message")
            if not isinstance(message, dict) or message.get("role", "assistant") != "assistant":
                raise ValueError("Provider returned an invalid assistant message")
            if message.get("function_call") is not None or data.get("function_call") is not None or message.get("refusal") is not None:
                raise ValueError("Legacy calls and refusals are not supported")
            if message.get("audio") is not None:
                raise ValueError("Only text and native function calls are supported")
            calls = validate_tool_calls(message.get("tool_calls", []))
            content = message.get("content")
            if not isinstance(content, str) and not (content is None and calls):
                raise ValueError("Provider returned invalid native content")
            finish_reason = choice.get("finish_reason")
            if finish_reason != ("tool_calls" if calls else "stop"):
                raise ValueError("Provider returned an incomplete or inconsistent finish reason")
            model = data.get("model", self.model_name)
            if not isinstance(model, str) or not model.strip():
                raise ValueError("Provider returned an invalid model")
        except ValueError as error:
            raise InvalidResponseError(str(error), *counts) from error
        assistant_message = {"role": "assistant", "content": content}
        if calls:
            assistant_message["tool_calls"] = message["tool_calls"]
        return ToolResponse(content or "", json.dumps(data, ensure_ascii=False), model, calls,
                            *counts, finish_reason, assistant_message)

    def _parse_response(self, data: dict) -> AdapterResponse:
        if not isinstance(data, dict):
            raise InvalidResponseError("Provider response must be an object")
        usage = data.get("usage")
        if usage is None:
            usage = {}
        if not isinstance(usage, dict):
            raise InvalidResponseError("Provider usage must be an object")
        counts = [usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)]
        if any(type(value) is not int or value < 0 for value in counts):
            raise InvalidResponseError("Provider usage must contain nonnegative token counts")
        choices = data.get("choices")
        if "error" in data or not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise InvalidResponseError("Provider returned an error or invalid choices", *counts)
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise InvalidResponseError("Provider returned an invalid text message", *counts)
        if message.get("tool_calls") not in (None, []) or message.get("function_call") is not None:
            raise InvalidResponseError("Model tools are not supported by text generation", *counts)
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise InvalidResponseError("Provider returned invalid text", *counts)
        return AdapterResponse(
            content=content,
            raw_content=json.dumps(data, ensure_ascii=False),
            model=data.get("model", self.model_name),
            tokens_input=counts[0],
            tokens_output=counts[1],
            finish_reason=choice.get("finish_reason", "stop"),
        )


class DeepSeekAdapter(OpenAICompatAdapter):
    provider = "deepseek"
    model_name = "deepseek-chat"

    def __init__(self, api_key: str):
        super().__init__(api_key, base_url="https://api.deepseek.com/v1", model_name="deepseek-chat")


class OllamaAdapter(OpenAICompatAdapter):
    provider = "ollama"

    def __init__(self, model_name: str = "qwen2.5:7b", base_url: str = "http://localhost:11434/v1"):
        super().__init__(api_key="ollama", base_url=base_url, model_name=model_name)

    def _build_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}  # Ollama needs no auth header
