"""Chat entry contracts over real localhost TCP/JWT/temporary SQL; paid calls 0."""
import asyncio
import hashlib
import json
import os
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from sqlalchemy import func, select
from app.api import agent_runs
from app.models import Conversation, Message, DocumentChunk, APICallLog
from tests.test_agent_readonly_http import native, configure, provider_reply, PATH
from tests.test_reading_native_stream_http_flow import server


@pytest.fixture(autouse=True)
def no_environment_model(monkeypatch):
    for name in ('OPENAI_API_KEY', 'DEEPSEEK_API_KEY'):
        monkeypatch.delenv(name, raising=False)


def record(request, data):
    out = Path(os.environ.get('KNOWALL_AGENT_CHAT_EVIDENCE_DIR', str(
        Path(__file__).resolve().parents[2] / 'docs/open-source-integration/native-agent-chat-2026-10-11/backend/http')))
    out.mkdir(parents=True, exist_ok=True)
    data.update(test=request.node.nodeid, at=datetime.now().astimezone().isoformat(),
                auth='real JWT/User dependency', database='temporary SQLite', paid_calls=0)
    (out / (hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16] + '.json')).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')


async def test_models_export_only_owned_and_global_without_credentials(native, request):
    n = native
    n.unified.configure_adapter('openai_alice', 'alice-secret', 'http://alice-secret.invalid/v1',
                                'personal-study', owner_user_id='alice')
    n.unified.configure_adapter('deepseek_bob', 'bob-secret', 'http://bob-secret.invalid/v1',
                                'private-bob-model', owner_user_id='bob')
    n.unified.configure_adapter('ollama', 'global-secret', 'http://global-secret.invalid/v1', 'global-study')
    # Older registrations without owner metadata must not become global models.
    n.unified._adapters['anthropic_bob'] = n.unified._adapters['deepseek_bob']
    response = await n.client.get(PATH + '/models', headers=n.headers('alice'))
    record(request, {'status':response.status_code, 'body':response.json(), 'provider_requests':0})
    assert response.status_code == 200, response.text
    assert response.json() == {'streaming_events':True,'background_runs':True,'models':[
        {'value':'ollama/global-study', 'label':'ollama / global-study', 'model_tool_support':'unverified'},
        {'value':'openai/personal-study', 'label':'openai / personal-study', 'model_tool_support':'unverified'}]}
    assert all(text not in response.text for text in ('secret', 'http:', 'bob', 'alice'))
    assert (await n.client.get(PATH + '/models')).status_code == 401


async def test_models_empty_is_valid_and_foreign_registry_stays_empty(native, request):
    native.unified.configure_adapter('openai_bob', 'not-used', model_name='private', owner_user_id='bob')
    response = await native.client.get(PATH + '/models', headers=native.headers('alice'))
    record(request, {'status':response.status_code, 'body':response.json(), 'provider_requests':0})
    assert response.status_code == 200 and response.json() == {'streaming_events':True,'background_runs':True,'models':[]}


async def test_explicit_global_custom_provider_is_discovered_without_foreign_legacy(native):
    n = native
    n.unified.configure_adapter('custom_gateway', 'not-used', model_name='global-model')
    n.unified._adapters['custom_other_account'] = n.unified._adapters['custom_gateway']
    response = await n.client.get(PATH + '/models', headers=n.headers('alice'))
    assert response.status_code == 200 and response.json() == {'streaming_events':True,'background_runs':True,'models':[
        {'value':'custom_gateway/global-model', 'label':'custom_gateway / global-model',
         'model_tool_support':'unverified'}]}


async def test_models_discover_environment_global_without_network(native, monkeypatch, request):
    monkeypatch.setenv('OPENAI_API_KEY', 'simulated-environment-secret')
    response = await native.client.get(PATH + '/models', headers=native.headers('alice'))
    record(request, {'status':response.status_code, 'body':response.json(), 'provider_requests':0})
    assert response.status_code == 200 and response.json() == {'streaming_events':True,'background_runs':True,'models':[
        {'value':'openai/gpt-4o', 'label':'openai / gpt-4o', 'model_tool_support':'unverified'}]}
    assert 'secret' not in response.text


async def test_personal_provider_shadows_global_choices_to_match_actual_routing(native):
    n = native
    n.unified.configure_adapter('openai', 'global-secret', 'http://global.invalid/v1', 'global-study')
    n.unified.configure_adapter('openai_alice', 'personal-secret', 'http://personal.invalid/v1',
                                'personal-study', owner_user_id='alice')
    response = await n.client.get(PATH + '/models', headers=n.headers('alice'))
    assert response.status_code == 200 and response.json() == {'streaming_events':True,'background_runs':True,'models':[
        {'value':'openai/personal-study', 'label':'openai / personal-study', 'model_tool_support':'unverified'}]}
    adapter = n.unified.get_agent_adapter(response.json()['models'][0]['value'], 'alice')
    assert adapter.base_url == 'http://personal.invalid/v1' and adapter.api_key == 'personal-secret'


@pytest.mark.parametrize('configured_model', ['gpt-4o-mini', 'custom-private-study'])
async def test_discovered_canonical_alias_only_registration_resolves_to_own_gateway(native, configured_model):
    n = native
    n.unified.configure_adapter('openai', 'global-secret', 'http://global.invalid/v1', 'global-study')
    n.unified.configure_adapter('gpt-4o-mini_alice', 'personal-secret', 'http://personal.invalid/v1',
                                configured_model, owner_user_id='alice')
    response = await n.client.get(PATH + '/models', headers=n.headers('alice'))
    assert response.json()['models'][0]['value'] == 'openai/' + configured_model
    adapter = n.unified.get_agent_adapter(response.json()['models'][0]['value'], 'alice')
    assert adapter.base_url == 'http://personal.invalid/v1' and adapter.api_key == 'personal-secret'


@pytest.mark.parametrize('configured_model', ['gpt-4o-mini', 'custom-global-study'])
async def test_discovered_global_alias_only_registration_resolves_without_canonical_base(native, configured_model):
    n = native
    n.unified.configure_adapter('gpt-4o-mini', 'global-secret', 'http://global-alias.invalid/v1', configured_model)
    response = await n.client.get(PATH + '/models', headers=n.headers('alice'))
    assert response.status_code == 200 and response.json()['models'][0]['value'] == 'openai/' + configured_model
    adapter = n.unified.get_agent_adapter(response.json()['models'][0]['value'], 'alice')
    assert adapter.base_url == 'http://global-alias.invalid/v1' and adapter.api_key == 'global-secret'


async def test_crafted_legacy_foreign_provider_is_rejected_before_any_native_call(native, request):
    n = native; provider = FastAPI(); calls = []
    @provider.post('/v1/chat/completions')
    async def model(req:Request):
        calls.append(await req.json())
        return provider_reply('openai', text='must not be reached')
    async with server(provider) as remote:
        n.unified.configure_adapter('openai_bob', 'explicit-simulated-bob-key',
            str(remote.base_url).rstrip('/') + '/v1', 'private-study')
        response = await n.client.post(PATH + '/answer', headers=n.headers('alice'),
            json={'message':'attempt namespace bypass', 'model':'openai_bob/private-study'})
        capability = await n.client.get(PATH + '/capabilities', headers=n.headers('alice'),
                                       params={'model':'openai_bob/private-study'})
    record(request, {'status':response.status_code, 'body':response.json(), 'provider_requests':len(calls),
                     'capability':capability.json(), 'registration':'legacy configure_adapter without owner'})
    assert response.status_code == 400 and calls == []
    assert capability.json()['available'] is False


async def test_models_reload_real_stored_keys_and_revoke(native, monkeypatch, tmp_path, request):
    import app.core.auth as auth
    import app.core.crypto as crypto
    import app.core.api_scheduler as scheduler
    from app.models import APIKey
    n = native
    async with n.factory.kw['bind'].begin() as conn:
        await conn.run_sync(APIKey.__table__.create)
    monkeypatch.setattr(crypto, '_KEY_FILE', tmp_path / 'disposable-secret')
    monkeypatch.setattr(scheduler, 'api_client', n.unified)
    monkeypatch.setattr(agent_runs, 'load_user_api_keys', auth.load_user_api_keys)
    async with n.factory() as db:
        db.add(APIKey(id='alice-key', user_id='alice', provider='openai',
                      key_encrypted=crypto.encrypt_api_key('explicit-simulated-alice-secret')))
        db.add(APIKey(id='bob-key', user_id='bob', provider='deepseek',
                      key_encrypted=crypto.encrypt_api_key('explicit-simulated-bob-secret')))
        await db.commit()
    response = await n.client.get(PATH + '/models', headers=n.headers('alice'))
    assert response.status_code == 200, response.text
    values = {item['value'] for item in response.json()['models']}
    assert values == {'openai/gpt-4o', 'openai/gpt-4o-mini'}
    async with n.factory() as db:
        key = await db.get(APIKey, 'alice-key'); key.is_active = False; await db.commit()
    revoked = await n.client.get(PATH + '/models', headers=n.headers('alice'))
    record(request, {'active':response.json(), 'revoked':revoked.json(), 'provider_requests':0})
    assert revoked.json() == {'streaming_events':True,'background_runs':True,'models':[]}


@pytest.mark.parametrize('chunk', ['missing', 'b-chunk'])
async def test_source_missing_and_foreign_are_same_404(native, chunk, request):
    response = await native.client.get(PATH + '/sources/' + chunk, params={'sha256':'a' * 64},
                                       headers=native.headers('alice'))
    record(request, {'status':response.status_code, 'body':response.json(), 'provider_requests':0})
    assert response.status_code == 404


async def test_source_sha_revalidation_and_retry_after_restoration(native, request):
    n = native; text = '细胞呼吸消耗氧气'; sha = hashlib.sha256(text.encode()).hexdigest()
    async def get(value=sha):
        return await n.client.get(PATH + '/sources/a-chunk', params={'sha256':value}, headers=n.headers('alice'))
    original = await get()
    assert original.status_code == 200, original.text
    assert original.json()['chunk_sha256'] == sha and original.json()['text'] == text
    async with n.factory() as db:
        chunk = await db.get(DocumentChunk, 'a-chunk'); chunk.text_content = '当前新版本'; await db.commit()
    changed = await get()
    assert changed.status_code == 409 and text not in changed.text
    current = await get(hashlib.sha256('当前新版本'.encode()).hexdigest())
    assert current.status_code == 200 and current.json()['text'] == '当前新版本'
    async with n.factory() as db:
        chunk = await db.get(DocumentChunk, 'a-chunk'); chunk.text_content = text; await db.commit()
    restored = await get()
    assert restored.status_code == 200 and restored.json()['text'] == text
    assert (await get('invalid')).status_code == 422
    assert (await n.client.get(PATH + '/sources/a-chunk', params={'sha256':sha})).status_code == 401
    record(request, {'original':original.json(), 'changed_status':changed.status_code,
                     'current':current.json(), 'restored':restored.json(), 'provider_requests':0})


@pytest.mark.parametrize('disconnect_at,application', [
    ('first-model','router'), ('final-model','router'), ('tool','router'),
    ('first-model','actual-main')])
async def test_actual_tcp_disconnect_cancels_immediately_and_no_next_step_or_messages(
        native, monkeypatch, disconnect_at, application, request):
    n = native; provider = FastAPI(); calls = []; tool_calls = []
    arrived = asyncio.Event(); release = asyncio.Event(); cancelled = asyncio.Event()
    original_run = agent_runs.run_readonly
    original_tool = agent_runs.DocumentToolBridge.execute
    async def watched_run(*args, **kwargs):
        try: return await original_run(*args, **kwargs)
        except asyncio.CancelledError:
            cancelled.set(); raise
    async def watched_tool(self, call_id, name, arguments):
        tool_calls.append(name)
        if disconnect_at == 'tool':
            arrived.set(); await release.wait()
        return await original_tool(self, call_id, name, arguments)
    monkeypatch.setattr(agent_runs, 'run_readonly', watched_run)
    monkeypatch.setattr(agent_runs.DocumentToolBridge, 'execute', watched_tool)
    @provider.post('/v1/chat/completions')
    async def model(req:Request):
        calls.append(await req.json())
        if disconnect_at == 'first-model' or (disconnect_at == 'final-model' and len(calls) == 2):
            arrived.set(); await release.wait()
        if len(calls) == 1:
            return provider_reply('openai', name='read_document_chunk', args={'chunk_id':'a-chunk'})
        return provider_reply('openai', text='消耗氧气 [S1]')
    async with AsyncExitStack() as stack:
        remote = await stack.enter_async_context(server(provider))
        await configure(n, remote, 'openai')
        transport = n.client
        if application == 'actual-main':
            from app.main import app
            from app.database import get_db
            async def disposable_database():
                async with n.factory() as db: yield db
            monkeypatch.setitem(app.dependency_overrides, get_db, disposable_database)
            transport = await stack.enter_async_context(server(app))
        reader, writer = await asyncio.open_connection(transport.base_url.host, transport.base_url.port)
        body = json.dumps({'message':'解释细胞呼吸', 'model':'openai/simulated-native'}).encode()
        headers = (f'POST {PATH}/answer HTTP/1.1\r\nHost: localhost\r\n'
                   f'Authorization: {n.headers("alice")["Authorization"]}\r\n'
                   f'Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n').encode()
        writer.write(headers + body); await writer.drain()
        try:
            await asyncio.wait_for(arrived.wait(), 5)
            writer.close(); await writer.wait_closed()
            # Cancellation must occur before the held provider/tool completes.
            await asyncio.wait_for(cancelled.wait(), 1)
            before = (len(calls), len(tool_calls))
        finally:
            release.set(); writer.close()
        await asyncio.sleep(.2)
        assert (len(calls), len(tool_calls)) == before
    async with n.factory() as db:
        message_rows = await db.scalar(select(func.count()).select_from(Message))
        conv_rows = await db.scalar(select(func.count()).select_from(Conversation))
        logs = (await db.scalars(select(APICallLog))).all()
    record(request, {'disconnect_at':disconnect_at, 'application':application, 'provider_requests':len(calls),
                     'tool_calls':tool_calls, 'messages':message_rows, 'conversations':conv_rows,
                     'cancel_observed_before_provider_release':cancelled.is_set(),
                     'call_logs':[{'success':item.success, 'error':item.error_message} for item in logs],
                     'limit':'urllib worker and already-started remote billing cannot be revoked'})
    assert message_rows == conv_rows == 0
    assert len(calls) == (2 if disconnect_at == 'final-model' else 1)
    assert len(tool_calls) == (0 if disconnect_at == 'first-model' else 1)
