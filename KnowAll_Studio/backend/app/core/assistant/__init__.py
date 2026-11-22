"""AI Multi-role Assistant (M6) - supports real SSE streaming"""
import json
import httpx
from httpx_sse import EventSource
from typing import AsyncGenerator
from app.core.api_scheduler import api_client, TaskType, GenerationConfig, RequestContext
from app.core.api_scheduler.adapters.base import AdapterConfig
from app.core.streaming import closing_stream, stream_resources

ROLE_PRESETS = {
    "lecturer": {
        "name": "授课讲师",
        "system": "你是一位经验丰富的授课讲师。请系统性地讲解知识点，用清晰的逻辑、生动的例子帮助学习者理解。在讲解结束后可以提一个思考题。",
    },
    "tutor": {
        "name": "答疑助教",
        "system": "你是一位耐心细致的答疑助教。针对学习者的问题给出准确、易懂的解答。如果不确定，请诚实说明。用分点方式组织回答。",
    },
    "mentor": {
        "name": "研讨主持人",
        "system": "你是一位苏格拉底式研讨主持人。不要直接给出答案，而是通过层层递进的提问引导学习者自己发现答案。每次回复最多提一个问题。",
    },
    "expand": {
        "name": "举一反三",
        "system": "你是一位知识拓展专家。根据学习者提供的内容，给出3-5个相关的拓展知识点、实际应用场景或跨学科联系。",
    },
}


def _stream_object(data: str) -> dict:
    """Validate model payloads; SSE framing belongs to httpx-sse."""
    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError("模型流返回了无效数据，请重新发送问题") from exc
    if not isinstance(value, dict):
        raise ValueError("模型流返回了无效数据，请重新发送问题")
    return value


class Assistant:
    """AI assistant with role presets. Handles conversation context management."""

    def get_role_presets(self) -> dict:
        return {
            key: {"name": v["name"], "description": v["system"][:50] + "..."}
            for key, v in ROLE_PRESETS.items()
        }

    async def chat_stream(
        self,
        user_message: str,
        role_preset: str = "tutor",
        history: list[dict] | None = None,
        model: str = "deepseek-chat",
        user_id: str | None = None,
        *,
        knowledge_context: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Stream chat response using SSE via httpx streaming."""
        from app.core.api_scheduler.adapters.anthropic import AnthropicAdapter

        preset = ROLE_PRESETS.get(role_preset, ROLE_PRESETS["tutor"])
        system = preset["system"]
        if knowledge_context:
            system += f"\n\n参考知识上下文：\n{knowledge_context[:3000]}"
        messages = [{"role": "system", "content": system}]
        if history:
            messages.extend(history[-20:])
        messages.append({"role": "user", "content": user_message})

        adapter = api_client.get_adapter(model, user_id=user_id)

        if isinstance(adapter, AnthropicAdapter):
            # Anthropic Messages API streaming format
            url = f"{adapter.base_url.rstrip('/')}/messages"
            headers = adapter._build_headers()
            headers.update({"anthropic-version": adapter._api_version, "Accept": "text/event-stream", "Cache-Control": "no-store"})
            # Convert to Anthropic message format
            system_content = None
            anthropic_msgs = []
            for msg in messages:
                if msg["role"] == "system":
                    system_content = msg["content"]
                else:
                    anthropic_msgs.append({"role": msg["role"], "content": [{"type": "text", "text": msg["content"]}]})
            payload = {
                "model": adapter.model_name,
                "max_tokens": 2048,
                "messages": anthropic_msgs,
                "stream": True,
            }
            if system_content:
                payload["system"] = [{"type": "text", "text": system_content}]

            async with stream_resources() as resources:
                client = await resources.enter_async_context(httpx.AsyncClient(timeout=120))
                response = await resources.enter_async_context(client.stream('POST', url, json=payload, headers=headers))
                response.raise_for_status()
                complete = has_text = False
                async with closing_stream(EventSource(response).aiter_sse()) as events:
                    async for sse in events:
                        if sse.event == "error":
                            raise ValueError("模型流返回错误，请重新发送问题")
                        if not sse.data:
                            continue
                        event = _stream_object(sse.data)
                        kind = event.get("type")
                        if not isinstance(kind, str):
                            raise ValueError("模型流返回了无效事件类型")
                        if kind == "error" or "error" in event:
                            raise ValueError("模型流返回错误，请重新发送问题")
                        if kind in ("content_block_delta", "message_delta"):
                            delta = event.get("delta")
                            if not isinstance(delta, dict):
                                raise ValueError("模型流返回了无效增量数据")
                            if kind == "message_delta":
                                reason = delta.get("stop_reason")
                                if reason is not None and reason not in ("end_turn", "stop_sequence"):
                                    raise ValueError("模型回答未完整结束，请重新发送问题")
                            elif delta.get("type") == "text_delta":
                                content = delta.get("text")
                                if not isinstance(content, str):
                                    raise ValueError("模型流未返回有效文本")
                                has_text = has_text or bool(content.strip())
                                if content:
                                    yield content
                            elif delta.get("type") == "input_json_delta":
                                raise ValueError("当前文本聊天无法完成模型工具调用")
                        elif kind == "message_stop":
                            complete = True
                            break
                        # Ignore future event types, as required by Anthropic.
                if not complete or not has_text:
                    raise ValueError("模型回答未完整结束或内容为空，请重新发送问题")
        else:
            # OpenAI-compatible SSE streaming
            url = f"{adapter.base_url.rstrip('/')}/chat/completions"
            headers = adapter._build_headers()
            headers.update({"Accept": "text/event-stream", "Cache-Control": "no-store"})
            payload = {
                "model": adapter.model_name,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 2048,
                "stream": True,
            }

            async with stream_resources() as resources:
                client = await resources.enter_async_context(httpx.AsyncClient(timeout=120))
                response = await resources.enter_async_context(client.stream('POST', url, json=payload, headers=headers))
                response.raise_for_status()
                complete = has_text = False
                async with closing_stream(EventSource(response).aiter_sse()) as events:
                    async for sse in events:
                        if sse.event == "error":
                            raise ValueError("模型流返回错误，请重新发送问题")
                        if not sse.data:
                            continue
                        if sse.data.strip() == "[DONE]":
                            complete = True
                            break
                        chunk = _stream_object(sse.data)
                        if "error" in chunk:
                            raise ValueError("模型流返回错误，请重新发送问题")
                        choices = chunk.get("choices")
                        if not isinstance(choices, list):
                            raise ValueError("模型流返回了无效选择数据")
                        if not choices and isinstance(chunk.get("usage"), dict):
                            continue  # Native usage-only final chunks have no choices.
                        if not choices or not isinstance(choices[0], dict):
                            raise ValueError("模型流返回了无效选择数据")
                        choice = choices[0]
                        delta = choice.get("delta", {})
                        if not isinstance(delta, dict):
                            raise ValueError("模型流返回了无效增量数据")
                        if delta.get("tool_calls") or delta.get("function_call"):
                            raise ValueError("当前文本聊天无法完成模型工具调用")
                        reason = choice.get("finish_reason")
                        if reason is not None and reason != "stop":
                            raise ValueError("模型回答未完整结束，请重新发送问题")
                        content = delta.get("content")
                        if content is not None and not isinstance(content, str):
                            raise ValueError("模型流未返回有效文本")
                        if content:
                            if complete:
                                raise ValueError("模型流在结束标记后仍返回文本")
                            has_text = has_text or bool(content.strip())
                            yield content
                        complete = complete or reason == "stop"
                # Compatible providers may omit finish_reason but supply DONE.
                if not complete or not has_text:
                    raise ValueError("模型回答未完整结束或内容为空，请重新发送问题")

    async def chat_stream_with_context(
        self,
        user_message: str,
        knowledge_context: str,
        role_preset: str = "tutor",
        history: list[dict] | None = None,
        model: str = "deepseek-chat",
        user_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Reuse the native stream protocols and the non-streaming context budget."""
        async with closing_stream(self.chat_stream(
            user_message, role_preset, history, model, user_id=user_id,
            knowledge_context=knowledge_context,
        )) as stream:
            async for chunk in stream:
                yield chunk

    async def chat(
        self,
        user_message: str,
        role_preset: str = "tutor",
        history: list[dict] | None = None,
        model: str = "deepseek-chat",
        user_id: str | None = None,
    ) -> str:
        """Non-streaming chat. Returns full response."""
        preset = ROLE_PRESETS.get(role_preset, ROLE_PRESETS["tutor"])
        messages = [{"role": "system", "content": preset["system"]}]
        if history:
            messages.extend(history[-20:])
        messages.append({"role": "user", "content": user_message})

        adapter = api_client.get_adapter(model, user_id=user_id)
        response = await adapter.chat_completion(
            messages,
            AdapterConfig(temperature=0.7, max_tokens=2048, timeout=120),
        )
        # AdapterResponse already normalizes provider completion metadata.
        # Persist only a complete text answer; never retry an incomplete call.
        if response.finish_reason not in ("stop", "end_turn", "stop_sequence"):
            raise ValueError("模型回答未完整结束，请重新发送问题")
        if not isinstance(response.content, str) or not response.content.strip():
            raise ValueError("模型未返回有效文本，请重新发送问题")
        return response.content

    async def chat_with_context(
        self,
        user_message: str,
        knowledge_context: str,
        role_preset: str = "tutor",
        history: list[dict] | None = None,
        model: str = "deepseek-chat",
        user_id: str | None = None,
    ) -> str:
        """Chat with additional knowledge context."""
        preset = ROLE_PRESETS.get(role_preset, ROLE_PRESETS["tutor"])
        messages = [
            {"role": "system", "content": preset["system"] + f"\n\n参考知识上下文：\n{knowledge_context[:3000]}"},
        ]
        if history:
            messages.extend(history[-20:])
        messages.append({"role": "user", "content": user_message})

        result = await api_client.generate(
            task_type=TaskType.CHAT,
            messages=messages,
            prompt_template_id=f"assistant.{role_preset}",
            generation_content=user_message,
            config=GenerationConfig(model=model, max_tokens=2048),
            context=RequestContext(user_id=user_id or "local_user"),
        )
        return result.content


assistant = Assistant()
