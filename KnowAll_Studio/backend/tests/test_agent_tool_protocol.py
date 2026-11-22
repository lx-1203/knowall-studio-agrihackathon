"""Native non-streaming protocol, using explicit simulated provider responses only."""
import copy
import json
from dataclasses import fields

import pytest

from app.core.api_scheduler.adapters import base
from app.core.api_scheduler.adapters.base import AdapterConfig, InvalidResponseError
from app.core.api_scheduler.adapters.openai import OpenAICompatAdapter
from app.core.api_scheduler.adapters.anthropic import AnthropicAdapter


TOOLS = [{"type": "function", "function": {"name": "lookup", "description": "Find a note",
    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}]
CALL = {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": '{"query":"笔记"}'}}
HISTORY = [{"role": "system", "content": "help"}, {"role": "user", "content": "find"},
    {"role": "assistant", "content": None, "tool_calls": [CALL]},
    {"role": "tool", "tool_call_id": "call_1", "content": "found"}]


def response(protocol, calls=True):
    if protocol == "openai":
        return {"model": "simulation", "choices": [{"message": {"role": "assistant", "content": None if calls else "done",
            "tool_calls": [copy.deepcopy(CALL)] if calls else []}, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 11}}
    return {"model": "simulation", "content": [{"type": "tool_use", "id": "call_1", "name": "lookup", "input": {"query": "笔记"}}] if calls else [{"type": "text", "text": "done"}],
        "stop_reason": "tool_use" if calls else "end_turn", "usage": {"input_tokens": 7, "output_tokens": 11}}


def adapter(protocol):
    cls = OpenAICompatAdapter if protocol == "openai" else AnthropicAdapter
    return cls("explicit-simulation", model_name="simulation")


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
def test_token_count_accepts_literal_special_marker_in_source_text(protocol):
    instance = adapter(protocol)
    text = 'A note quotes <|endoftext|> as ordinary source text.'
    count = instance.count_tokens(text)
    assert count == len(instance._encoder.encode(text, disallowed_special=()))
    assert count > 0


def transport(monkeypatch, protocol, data, requests):
    def post(url, headers, payload, timeout):
        requests.append(copy.deepcopy(payload))
        return data
    monkeypatch.setattr("app.core.api_scheduler.adapters." + protocol + ".sync_post", post)


def test_native_contract_has_required_fields():
    assert hasattr(base, "ToolCall"), "Native ToolCall contract is missing"
    assert hasattr(base, "ToolResponse"), "Native ToolResponse contract is missing"
    assert [f.name for f in fields(base.ToolCall)] == ["call_id", "name", "arguments"]
    assert [f.name for f in fields(base.ToolResponse)] == ["content", "raw_content", "model", "tool_calls", "tokens_input", "tokens_output", "finish_reason", "assistant_message"]


async def test_unsupported_adapter_is_explicit():
    class Unsupported(base.BaseModelAdapter):
        async def chat_completion(self, messages, config):
            raise AssertionError("must not fall back to text")
        def count_tokens(self, text):
            return 0
    instance = Unsupported("simulation")
    assert hasattr(instance, "chat_completion_with_tools"), "Native tool path is missing"
    with pytest.raises(NotImplementedError):
        await instance.chat_completion_with_tools([], TOOLS, AdapterConfig())


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("calls", [True, False])
async def test_native_response_and_fixed_nonstream_request(monkeypatch, protocol, calls):
    instance = adapter(protocol)
    assert hasattr(instance, "chat_completion_with_tools"), "Native tool path is missing"
    requests = []
    data = response(protocol, calls)
    transport(monkeypatch, protocol, data, requests)
    result = await instance.chat_completion_with_tools(copy.deepcopy(HISTORY), TOOLS,
        AdapterConfig(extra={"stream": True, "tool_choice": "none", "tools": [], "messages": [], "functions": []}))
    assert result.content == ("" if calls else "done")
    assert result.tokens_input == 7 and result.tokens_output == 11
    assert result.model == "simulation" and json.loads(result.raw_content) == data
    assert [(c.call_id, c.name, c.arguments) for c in result.tool_calls] == ([('call_1', 'lookup', {'query': '笔记'})] if calls else [])
    assert result.finish_reason == (('tool_calls' if protocol == 'openai' else 'tool_use') if calls else ('stop' if protocol == 'openai' else 'end_turn'))
    request = requests[0]
    assert request['stream'] is False and 'functions' not in request
    if protocol == 'openai':
        assert request['tool_choice'] == 'auto' and request['tools'] == TOOLS
        assert request['messages'] == HISTORY
    else:
        assert request['tool_choice'] == {'type': 'auto'}
        assert request['tools'] == [{'name': 'lookup', 'description': 'Find a note', 'input_schema': TOOLS[0]['function']['parameters']}]
        assert request['system'] == 'help'
        assert request['messages'] == [{'role': 'user', 'content': 'find'},
            {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'call_1', 'name': 'lookup', 'input': {'query': '笔记'}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'call_1', 'content': 'found'}]}]


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
@pytest.mark.parametrize('bad', ['duplicate', 'empty-id', 'empty-name', 'array-args', 'null-args', 'malformed-args',
    'legacy', 'unknown', 'bad-content', 'bad-stop', 'error', 'bool-usage', 'negative-usage', 'fraction-usage', 'usage-list', 'refusal', 'call-with-stop', 'no-call-with-tool-stop', 'null-without-call'])
async def test_bad_native_payload_is_rejected_with_validated_usage(monkeypatch, protocol, bad):
    instance = adapter(protocol)
    assert hasattr(instance, 'chat_completion_with_tools'), 'Native tool path is missing'
    data = response(protocol)
    if protocol == 'openai':
        choice = data['choices'][0]; message = choice['message']; call = message['tool_calls'][0]
        if bad == 'duplicate': message['tool_calls'].append(copy.deepcopy(call))
        if bad == 'empty-id': call['id'] = ' '
        if bad == 'empty-name': call['function']['name'] = ''
        if bad in ('array-args', 'null-args', 'malformed-args'): call['function']['arguments'] = {'array-args': '[]', 'null-args': 'null', 'malformed-args': '{'}[bad]
        if bad == 'legacy': message['function_call'] = {'name': 'lookup'}
        if bad == 'unknown': call['type'] = 'custom'
        if bad == 'bad-content': message['content'] = [{'type': 'text', 'text': 'bad'}]
        if bad == 'bad-stop': choice['finish_reason'] = 'length'
        if bad == 'refusal': message['refusal'] = 'no'
        if bad == 'call-with-stop': choice['finish_reason'] = 'stop'
        if bad == 'no-call-with-tool-stop': message['tool_calls'] = []; message['content'] = 'done'
        if bad == 'null-without-call': message['tool_calls'] = []; choice['finish_reason'] = 'stop'
        count_key = 'prompt_tokens'
    else:
        call = data['content'][0]
        if bad == 'duplicate': data['content'].append(copy.deepcopy(call))
        if bad == 'empty-id': call['id'] = ' '
        if bad == 'empty-name': call['name'] = ''
        if bad in ('array-args', 'null-args', 'malformed-args'): call['input'] = {'array-args': [], 'null-args': None, 'malformed-args': '{'}[bad]
        if bad == 'legacy': data['function_call'] = {'name': 'lookup'}
        if bad == 'unknown': call['type'] = 'server_tool_use'
        if bad == 'bad-content': data['content'].append({'type': 'text', 'text': None})
        if bad == 'bad-stop': data['stop_reason'] = 'max_tokens'
        if bad == 'refusal': data['stop_reason'] = 'refusal'
        if bad == 'call-with-stop': data['stop_reason'] = 'end_turn'
        if bad == 'no-call-with-tool-stop': data['content'] = [{'type': 'text', 'text': 'done'}]
        if bad == 'null-without-call': data['content'] = None; data['stop_reason'] = 'end_turn'
        count_key = 'input_tokens'
    if bad == 'error': data['error'] = None
    if bad == 'bool-usage': data['usage'][count_key] = True
    if bad == 'negative-usage': data['usage'][count_key] = -1
    if bad == 'fraction-usage': data['usage'][count_key] = 1.1
    if bad == 'usage-list': data['usage'] = []
    transport(monkeypatch, protocol, data, [])
    with pytest.raises(InvalidResponseError) as error:
        await instance.chat_completion_with_tools([{'role': 'user', 'content': 'find'}], TOOLS, AdapterConfig())
    expected = (0, 0) if bad in ('bool-usage', 'negative-usage', 'fraction-usage', 'usage-list') else (7, 11)
    assert (error.value.tokens_input, error.value.tokens_output) == expected


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
async def test_native_multiple_calls_and_results_are_preserved(monkeypatch, protocol):
    instance = adapter(protocol)
    assert hasattr(instance, 'chat_completion_with_tools'), 'Native tool path is missing'
    history = copy.deepcopy(HISTORY)
    second = copy.deepcopy(CALL); second['id'] = 'call_2'
    history[2]['tool_calls'].append(second)
    history.append({'role': 'tool', 'tool_call_id': 'call_2', 'content': 'second'})
    data = response(protocol); requests = []
    if protocol == 'openai': data['choices'][0]['message']['tool_calls'].append(second)
    else:
        second_block = copy.deepcopy(data['content'][0]); second_block['id'] = 'call_2'; data['content'].append(second_block)
    transport(monkeypatch, protocol, data, requests)
    result = await instance.chat_completion_with_tools(history, TOOLS, AdapterConfig())
    assert [c.call_id for c in result.tool_calls] == ['call_1', 'call_2']
    if protocol == 'anthropic':
        assert len(requests[0]['messages']) == 3
        assert [b['tool_use_id'] for b in requests[0]['messages'][-1]['content']] == ['call_1', 'call_2']


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
async def test_native_request_rejects_invalid_tool_schema_before_transport(monkeypatch, protocol):
    instance = adapter(protocol)
    assert hasattr(instance, 'chat_completion_with_tools'), 'Native tool path is missing'
    requests = []; transport(monkeypatch, protocol, response(protocol), requests)
    with pytest.raises(ValueError):
        await instance.chat_completion_with_tools([{'role': 'user', 'content': 'find'}], [{'type': 'custom'}], AdapterConfig())
    assert not requests


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
async def test_text_path_still_rejects_native_calls(monkeypatch, protocol):
    transport(monkeypatch, protocol, response(protocol), [])
    with pytest.raises(InvalidResponseError):
        await adapter(protocol).chat_completion([{'role': 'user', 'content': 'find'}], AdapterConfig())


async def test_anthropic_thinking_roundtrip_preserves_signature_without_text_leak(monkeypatch):
    instance = adapter('anthropic')
    assert hasattr(instance, 'chat_completion_with_tools'), 'Native tool path is missing'
    data = response('anthropic')
    data['content'].insert(0, {'type': 'thinking', 'thinking': 'private provider reasoning', 'signature': 'signed-by-provider'})
    data['content'].insert(1, {'type': 'redacted_thinking', 'data': 'opaque-provider-data'})
    data['content'].insert(2, {'type': 'text', 'text': 'looking'})
    requests = []; transport(monkeypatch, 'anthropic', data, requests)
    result = await instance.chat_completion_with_tools([{'role': 'user', 'content': 'find'}], TOOLS, AdapterConfig())
    assert result.content == 'looking' and 'private provider reasoning' not in result.content
    assert result.assistant_message['_anthropic_content'] == data['content']
    transport(monkeypatch, 'anthropic', response('anthropic', False), requests)
    await instance.chat_completion_with_tools([{'role': 'user', 'content': 'find'}, result.assistant_message,
        {'role': 'tool', 'tool_call_id': 'call_1', 'content': 'found'}], TOOLS, AdapterConfig())
    assert requests[-1]['messages'][1]['content'] == data['content']


@pytest.mark.parametrize('bad', ['NaN', 'Infinity', '-Infinity', '1e400'])
async def test_openai_native_arguments_are_strict_json(monkeypatch, bad):
    instance = adapter('openai')
    assert hasattr(instance, 'chat_completion_with_tools'), 'Native tool path is missing'
    data = response('openai'); data['choices'][0]['message']['tool_calls'][0]['function']['arguments'] = '{"value":' + bad + '}'
    transport(monkeypatch, 'openai', data, [])
    with pytest.raises(InvalidResponseError):
        await instance.chat_completion_with_tools([{'role': 'user', 'content': 'find'}], TOOLS, AdapterConfig())


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
@pytest.mark.parametrize('bad', ['orphan', 'missing-result', 'duplicate-result', 'result-nontext', 'legacy', 'content-list', 'reused-id', 'missing-content'])
async def test_native_history_rejects_invalid_roundtrip_before_transport(monkeypatch, protocol, bad):
    history = copy.deepcopy(HISTORY)
    if bad == 'orphan': history[3]['tool_call_id'] = 'unknown'
    if bad == 'missing-result': history.pop()
    if bad == 'duplicate-result': history.append(copy.deepcopy(history[-1]))
    if bad == 'result-nontext': history[3]['content'] = {'value': 'found'}
    if bad == 'legacy': history[2]['function_call'] = {'name': 'lookup'}
    if bad == 'content-list': history[1]['content'] = [{'type': 'text', 'text': 'find'}]
    if bad == 'reused-id': history.extend(copy.deepcopy(history[2:]))
    if bad == 'missing-content': history[2].pop('content')
    requests = []; transport(monkeypatch, protocol, response(protocol), requests)
    with pytest.raises(ValueError):
        await adapter(protocol).chat_completion_with_tools(history, TOOLS, AdapterConfig())
    assert requests == []


async def test_anthropic_replay_does_not_modify_history_when_merging_assistant_turns(monkeypatch):
    data = response('anthropic', False); instance = adapter('anthropic'); requests = []
    transport(monkeypatch, 'anthropic', data, requests)
    result = await instance.chat_completion_with_tools([{'role': 'user', 'content': 'find'}], TOOLS, AdapterConfig())
    history = [{'role': 'user', 'content': 'find'}, result.assistant_message,
        {'role': 'assistant', 'content': 'another'}]
    original = copy.deepcopy(history)
    transport(monkeypatch, 'anthropic', response('anthropic', False), requests)
    await instance.chat_completion_with_tools(history, TOOLS, AdapterConfig())
    assert history == original


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
async def test_native_http_transport_sends_tool_result_to_local_provider(protocol):
    """Real urllib/thread/JSON boundary with a localhost server; no model service."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            body = json.dumps(response(protocol, False)).encode('utf8')
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True); worker.start()
    try:
        cls = OpenAICompatAdapter if protocol == 'openai' else AnthropicAdapter
        instance = cls('explicit-simulation', base_url=f'http://127.0.0.1:{server.server_port}/v1', model_name='simulation')
        result = await instance.chat_completion_with_tools(copy.deepcopy(HISTORY), TOOLS, AdapterConfig(timeout=5))
        assert result.content == 'done' and result.tool_calls == []
        assert received[0][0] == ('/v1/chat/completions' if protocol == 'openai' else '/v1/messages')
        assert received[0][1]['stream'] is False
    finally:
        server.shutdown(); worker.join(timeout=5); server.server_close()
