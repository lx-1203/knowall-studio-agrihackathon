"""Real assistant HTTPX decoding and localhost routes, simulated model only."""
import json
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from app.core.assistant import Assistant, api_client
from app.core.rag_assistant import RAGAssistant
from app.core import rag_assistant as rag_module
from app.api import chat
from app.database import get_db
from app.core.auth import get_optional_user
from app.models import Message
from tests.test_round51_context_stream import install_adapter
from tests.test_round51_stream_lifetime import disk
from tests.test_reading_native_stream_http_flow import server


def frame(value):
    return 'data: ' + json.dumps(value, ensure_ascii=False) + '\n\n'

TOKEN = frame({'choices': [{'delta': {'content': '回答'}}]})
STOP = frame({'choices': [{'delta': {}, 'finish_reason': 'stop'}]})
DONE = 'data: [DONE]\n\n'
ATOKEN = frame({'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': '回答'}})
ASTOP = frame({'type': 'message_stop'})

FAILURES = [
    ('openai', TOKEN, 'eof'),
    ('openai', TOKEN + 'data: {broken}\n\n' + DONE, 'bad-json'),
    ('openai', TOKEN + frame({'error': {'message': 'secret-provider-error'}}) + DONE, 'error'),
    *[('openai', TOKEN + frame({'choices': [{'delta': {}, 'finish_reason': r}]}) + DONE, r) for r in ['length', 'content_filter', 'tool_calls', 'function_call', 'unknown']],
    ('openai', frame({'choices': [{'delta': {'content': 7}}]}) + DONE, 'non-text'),
    ('openai', frame({'choices': [{'delta': {'content': '  \n'}}]}) + DONE, 'whitespace'),
    ('openai', DONE, 'empty'),
    ('openai', frame({'choices': []}) + DONE, 'empty-choices'),
    ('openai', TOKEN + frame([]) + DONE, 'bad-object'),
    ('openai', TOKEN + frame({'choices': [{'delta': []}]}) + DONE, 'bad-delta'),
    ('anthropic', ATOKEN, 'eof'),
    ('anthropic', ATOKEN + 'data: {broken}\n\n' + ASTOP, 'bad-json'),
    ('anthropic', ATOKEN + frame({'type': 'error', 'error': {'message': 'secret-provider-error'}}) + ASTOP, 'error'),
    *[('anthropic', ATOKEN + frame({'type': 'message_delta', 'delta': {'stop_reason': r}}) + ASTOP, r) for r in ['max_tokens', 'tool_use', 'pause_turn', 'refusal', 'unknown']],
    ('anthropic', frame({'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': False}}) + ASTOP, 'non-text'),
    ('anthropic', ASTOP, 'empty'),
    ('anthropic', ATOKEN + 'event: error\ndata: {}\n\n' + ASTOP, 'named-error'),
    ('openai', TOKEN + frame({'choices': [{'delta': {'tool_calls': [{'index': 0}]}}]}) + DONE, 'tool-delta-without-reason'),
    ('openai', TOKEN + STOP + TOKEN + DONE, 'text-after-stop'),
    ('anthropic', ATOKEN + frame({'type': ['bad-type']}) + ASTOP, 'bad-type'),
    ('anthropic', ATOKEN + frame({'type': 'content_block_delta', 'delta': {'type': 'input_json_delta', 'partial_json': '{}'}}) + ASTOP, 'tool-delta-without-reason'),
]
SUCCESSES = [
    ('openai', TOKEN + DONE, 'legacy-done'),
    ('openai', TOKEN + STOP, 'stop-eof'),
    ('openai', TOKEN + STOP + frame({'choices': [], 'usage': {'total_tokens': 2}}) + DONE, 'usage'),
    ('openai', (TOKEN + DONE).replace('data: ', 'data:').replace('\n', '\r\n'), 'crlf-no-space'),
    ('openai', 'data: {"choices":\ndata: [{"delta":{"content":"回答"}}]}\n\n' + DONE, 'multi-data'),
    ('openai', frame({'choices': [{'delta': {'role': 'assistant', 'content': None}}]}) + TOKEN + DONE, 'role'),
    ('anthropic', ATOKEN + ASTOP, 'legacy-stop'),
    *[('anthropic', ATOKEN + frame({'type': 'message_delta', 'delta': {'stop_reason': r}}) + ASTOP, r) for r in ['end_turn', 'stop_sequence']],
    ('anthropic', frame({'type': 'ping'}) + frame({'type': 'future_event'}) + ATOKEN + ASTOP, 'forward-compatible'),
    ('anthropic', (ATOKEN + ASTOP).replace('data: ', 'data:'), 'no-space'),
]


def transport(monkeypatch, protocol, body, mime='text/event-stream'):
    original = httpx.AsyncClient; clients = []; responses = []; requests = []
    def handler(req):
        requests.append(req)
        response = httpx.Response(200, content=body.encode(), headers={'content-type': mime})
        responses.append(response); return response
    def factory(*args, **kwargs):
        obj = original(*args, transport=httpx.MockTransport(handler), **kwargs); clients.append(obj); return obj
    remote = SimpleNamespace(base_url='http://explicit-simulation')
    install_adapter(monkeypatch, remote, protocol, [])
    monkeypatch.setattr(httpx, 'AsyncClient', factory)
    return clients, responses, requests


@pytest.mark.parametrize('protocol,body,label', FAILURES, ids=[p + '-' + label for p, _, label in FAILURES])
async def test_reject_incomplete_or_nontext_chat(monkeypatch, protocol, body, label):
    clients, responses, _ = transport(monkeypatch, protocol, body)
    with pytest.raises(ValueError):
        _ = [part async for part in Assistant().chat_stream('question')]
    assert all(c.is_closed for c in clients) and all(r.is_closed for r in responses)


@pytest.mark.parametrize('protocol,body,label', SUCCESSES, ids=[p + '-' + label for p, _, label in SUCCESSES])
async def test_valid_native_sse_compatibility(monkeypatch, protocol, body, label):
    clients, responses, requests = transport(monkeypatch, protocol, body)
    assert ''.join([part async for part in Assistant().chat_stream_with_context('question', 'context')]) == '回答'
    assert all(c.is_closed for c in clients) and all(r.is_closed for r in responses)
    if protocol == 'anthropic':
        assert requests[0].headers['anthropic-version'] == '2023-06-01'


async def test_non_sse_http_success_rejected(monkeypatch):
    clients, responses, _ = transport(monkeypatch, 'openai', TOKEN + DONE, 'application/json')
    with pytest.raises(httpx.TransportError):
        _ = [part async for part in Assistant().chat_stream('question')]
    assert clients[0].is_closed and responses[0].is_closed


@pytest.mark.parametrize('context', ['', 'references'])
async def test_pipeline_model_error_never_retries_after_partial(monkeypatch, context):
    class Pipeline:
        async def search(self, *args, **kwargs): return SimpleNamespace(context=context)
    calls = []
    async def model(*args, **kwargs):
        calls.append(args); yield 'partial'; raise ValueError('stream incomplete')
    monkeypatch.setattr(rag_module, 'get_pipeline_by_name', lambda _: Pipeline())
    monkeypatch.setattr(rag_module.base_assistant, 'chat_stream', model)
    seen = []
    with pytest.raises(ValueError):
        async for part in RAGAssistant().chat_stream_with_rag('question', mode='registered-provider'):
            seen.append(part)
    assert seen == ['partial'] and len(calls) == 1


async def test_pipeline_retrieval_failure_keeps_one_base_call(monkeypatch):
    def broken(_): raise RuntimeError('retrieval unavailable')
    calls = []
    async def model(*args, **kwargs): calls.append(args); yield 'complete'
    monkeypatch.setattr(rag_module, 'get_pipeline_by_name', broken)
    monkeypatch.setattr(rag_module.base_assistant, 'chat_stream', model)
    assert [s async for s in RAGAssistant().chat_stream_with_rag('question', mode='registered-provider')] == ['complete']
    assert len(calls) == 1 and calls[0][0] == 'question'


@pytest.mark.parametrize('route,context', [('assistant/stream', None), ('assistant/stream', '引用'), ('assistant/rag/stream', None), ('assistant/graphrag/stream', None)])
@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
@pytest.mark.parametrize('failure', ['eof', 'length', 'bad-json'])
async def test_native_tcp_failure_then_retry_sql_once(disk, monkeypatch, route, context, protocol, failure):
    calls = []
    provider = FastAPI()
    @provider.post('/v1/chat/completions')
    @provider.post('/v1/messages')
    async def simulation(req: Request):
        calls.append(await req.json())
        body = TOKEN if protocol == 'openai' else ATOKEN
        if len(calls) == 2: body += DONE if protocol == 'openai' else ASTOP
        elif failure == 'bad-json': body += 'data: {broken}\n\n' + (DONE if protocol == 'openai' else ASTOP)
        elif failure == 'length':
            body += frame({'choices': [{'delta': {}, 'finish_reason': 'length'}]}) + DONE if protocol == 'openai' else frame({'type': 'message_delta', 'delta': {'stop_reason': 'max_tokens'}}) + ASTOP
        return StreamingResponse(iter([body]), media_type='text/event-stream')
    app = FastAPI(); app.include_router(chat.router)
    async def database():
        async with disk() as db: yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_optional_user] = lambda: None
    monkeypatch.setattr(rag_module, 'rag_query', lambda *args: '')
    import app.core.graph_rag as graph
    async def empty_graph(**kwargs): return '', []
    monkeypatch.setattr(graph, 'graph_rag_query', empty_graph)
    async with server(provider) as remote:
        install_adapter(monkeypatch, remote, protocol, [])
        async with server(app) as client:
            payload = {'message': 'question'}
            if context: payload['knowledge_context'] = context
            first = await client.post('/api/v1/chat/' + route, json=payload)
            failed = [json.loads(l[6:]) for l in first.text.splitlines() if l.startswith('data: ')]
            assert first.status_code == 200 and len([e for e in failed if 'error' in e]) == 1
            assert ''.join(e.get('token', '') for e in failed) == '回答'
            assert not any(e.get('done') and 'error' not in e for e in failed)
            async with disk() as db:
                assert not (await db.execute(select(Message).where(Message.role == 'assistant'))).scalars().all()
                users = (await db.execute(select(Message).where(Message.role == 'user'))).scalars().all()
                assert len(users) == 1
                payload['conversation_id'] = users[0].conversation_id
            second = await client.post('/api/v1/chat/' + route, json=payload)
    events = [json.loads(l[6:]) for l in second.text.splitlines() if l.startswith('data: ')]
    assert not any('error' in e for e in events) and sum(e.get('done', False) for e in events) == 1
    assert len(calls) == 2
    async with disk() as db:
        messages = (await db.execute(select(Message).where(Message.role == 'assistant'))).scalars().all()
        assert len(messages) == 1 and messages[0].content == '回答'
