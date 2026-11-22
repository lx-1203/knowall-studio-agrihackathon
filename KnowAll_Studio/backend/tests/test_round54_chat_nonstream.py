"""Native provider TCP/adapter/JWT/SQLite chain; no paid model invocation."""
import json
from pathlib import Path
import hashlib, os
from datetime import datetime
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import select, func
from app.api import chat
from app.models import Conversation, Message
from app.core.assistant import Assistant, api_client
from app.core.api_scheduler.adapters.openai import OpenAICompatAdapter
from app.core.api_scheduler.adapters.base import AdapterResponse
from tests.test_round53_chat_tenancy import tenancy, create, PATH
from tests.test_reading_native_stream_http_flow import server

CASES = [('length', '半截回答'), ('content_filter', '半截回答'), ('tool_calls', '半截回答'),
         ('unknown', '半截回答'), (None, '半截回答'), ('stop', ''), ('stop', '   '), ('stop', '完整回答')]

@pytest.mark.parametrize('resume', [False, True])
@pytest.mark.parametrize('reason,content', CASES)
async def test_real_native_nonstream_completion_before_sql_commit(tenancy, monkeypatch, reason, content, resume, request):
    t = tenancy
    old = await create(t) if resume else None
    async with t.factory() as db:
        before_messages = (await db.scalars(select(Message))).all()
        before_conversations = await db.scalar(select(func.count()).select_from(Conversation))
        before = [(m.id, m.content) for m in before_messages]
    provider = FastAPI()
    calls = []
    @provider.post('/v1/chat/completions')
    async def simulation(req: Request):
        calls.append(await req.json())
        return {'choices': [{'message': {'content': content}, 'finish_reason': reason}], 'usage': {}, 'model': 'test-model'}
    monkeypatch.setattr(chat.ai_assistant, 'chat', Assistant.chat.__get__(chat.ai_assistant))
    async with server(provider) as remote:
        adapter = OpenAICompatAdapter('explicit-simulation', base_url=str(remote.base_url).rstrip('/') + '/v1', model_name='test-model')
        monkeypatch.setattr(api_client, 'get_adapter', lambda *args, **kw: adapter)
        payload = {'message': 'new private question', **({'conversation_id': old} if old else {})}
        response = await t.client.post(PATH + '/assistant', headers=t.headers('alice'), json=payload)
        good = reason == 'stop' and bool(content.strip())
        async with t.factory() as db:
            messages = [(m.id, m.content) for m in (await db.scalars(select(Message))).all()]
            conversations = await db.scalar(select(func.count()).select_from(Conversation))
        evidence = {'at': datetime.now().astimezone().isoformat(), 'test': request.node.nodeid,
            'scope': 'native OpenAICompatAdapter sync_post thread -> localhost simulated provider; real JWT/SQL/chat route',
            'response': {'status': response.status_code, 'body': response.text}, 'provider_calls': len(calls),
            'before': before, 'after': messages, 'before_conversations': before_conversations, 'after_conversations': conversations,
            'assistant_sha256': hashlib.sha256(Path(__import__('app.core.assistant', fromlist=['']).__file__).read_bytes()).hexdigest(), 'paid_calls': 0}
        out = Path(os.environ['KNOWALL_READING_NATIVE_EVIDENCE_DIR']); out.mkdir(parents=True, exist_ok=True)
        (out / ('chat-' + hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16] + '.json')).write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf8')
        assert len(calls) == 1
        if good:
            assert response.status_code == 200 and response.json()['message'] == content
            assert len(messages) == len(before) + 2
            assert conversations == before_conversations + (0 if resume else 1)
        else:
            assert response.status_code == 500, response.text
            assert messages == before and conversations == before_conversations

@pytest.mark.parametrize('reason', ['end_turn', 'stop_sequence'])
async def test_standardized_anthropic_completion_remains_compatible(monkeypatch, reason):
    calls = []
    class Adapter:
        async def chat_completion(self, *args):
            calls.append(args)
            return AdapterResponse(content='完整回答', raw_content='{}', model='test', finish_reason=reason)
    monkeypatch.setattr(api_client, 'get_adapter', lambda *a, **kw: Adapter())
    assert await Assistant().chat('question', user_id='alice') == '完整回答'
    assert len(calls) == 1
