"""Native localhost HTTPX/assistant and SQLite routes; all model output simulated."""
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from app.api import chat
from app.core.assistant import Assistant, api_client
from app.core.api_scheduler.adapters.anthropic import AnthropicAdapter
from app.database import get_db
from app.core.auth import get_optional_user
from app.models import Message
from tests.test_round51_stream_lifetime import disk
from tests.test_reading_native_stream_http_flow import server


def provider(protocol, received):
    app = FastAPI()
    @app.post('/v1/chat/completions')
    @app.post('/v1/messages')
    async def simulation(req: Request):
        received.append({'path': req.url.path, 'payload': await req.json()})
        if protocol == 'anthropic':
            body = 'data: ' + json.dumps({'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': '完整回答'}}) + '\n\ndata: {"type":"message_stop"}\n\n'
        else:
            body = 'data: ' + json.dumps({'choices': [{'delta': {'content': '完整回答'}}]}) + '\n\ndata: [DONE]\n\n'
        return StreamingResponse(iter([body]), media_type='text/event-stream')
    return app


def install_adapter(monkeypatch, remote, protocol, selected):
    base = str(remote.base_url).rstrip('/') + '/v1'
    adapter = AnthropicAdapter('explicit-simulation', base, 'simulated-model') if protocol == 'anthropic' else SimpleNamespace(base_url=base, model_name='simulated-model', _build_headers=lambda: {'Authorization': 'Bearer explicit-simulation'})
    def choose(model, user_id=None):
        selected.append((model, user_id))
        return adapter
    monkeypatch.setattr(api_client, 'get_adapter', choose)


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
@pytest.mark.parametrize('context', [None, '知识' * 2000], ids=['plain', 'context'])
async def test_existing_stream_protocol_reused_with_context_budget(monkeypatch, protocol, context):
    received, selected = [], []
    async with server(provider(protocol, received)) as remote:
        install_adapter(monkeypatch, remote, protocol, selected)
        assistant = Assistant()
        history = [{'role': 'user', 'content': str(i)} for i in range(25)]
        stream = assistant.chat_stream('question', 'mentor', history, 'chosen-model', user_id='temporary-user') if context is None else assistant.chat_stream_with_context('question', context, 'mentor', history, 'chosen-model', user_id='temporary-user')
        assert ''.join([part async for part in stream]) == '完整回答'
    assert selected == [('chosen-model', 'temporary-user')]
    payload = received[0]['payload']
    assert payload['model'] == 'simulated-model' and payload['stream'] is True
    messages = payload['messages']
    system = payload['system'][0]['text'] if protocol == 'anthropic' else messages.pop(0)['content']
    if context is None:
        assert '参考知识上下文' not in system
    else:
        assert system.endswith(context[:3000]) and context[:3001] not in system
    assert len(messages) == 21
    content = lambda msg: msg['content'][0]['text'] if protocol == 'anthropic' else msg['content']
    assert content(messages[0]) == '5' and content(messages[-1]) == 'question'


async def test_native_context_chat_route_stream_and_single_sql_save(disk, monkeypatch):
    received, selected = [], []
    app = FastAPI()
    app.include_router(chat.router)
    async def database():
        async with disk() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_optional_user] = lambda: None
    async with server(provider('openai', received)) as remote:
        install_adapter(monkeypatch, remote, 'openai', selected)
        async with server(app) as client:
            response = await client.post('/api/v1/chat/assistant/stream', json={'message': 'question', 'knowledge_context': '引用内容'})
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert response.status_code == 200 and not any('error' in event for event in events), events
    assert ''.join(event.get('token', '') for event in events) == '完整回答'
    assert len([event for event in events if event.get('done')]) == 1
    assert selected == [('deepseek-chat', 'local_user')]
    async with disk() as db:
        messages = (await db.execute(select(Message))).scalars().all()
        assert sorted((m.role, m.content) for m in messages) == [('assistant', '完整回答'), ('user', 'question')]
