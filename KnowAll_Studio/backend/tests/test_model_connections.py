"""Persistent model configuration with an isolated database and local provider."""
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base, get_db
from app.models import User, APIKey
from app.core.auth import create_access_token, load_user_api_keys
from app.core.api_scheduler.client import UnifiedAPIClient


@pytest.fixture
async def connections(tmp_path, monkeypatch):
    from app.api import model_connections
    import app.core.api_scheduler as scheduler
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "connections.db"}')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([User(id=name, username=name, email=name+'@test.invalid', password_hash='unused') for name in ('alice', 'bob', 'local_user')])
        await db.commit()
    client = UnifiedAPIClient()
    monkeypatch.setattr(scheduler, 'api_client', client)
    app = FastAPI(); app.include_router(model_connections.router)
    async def database():
        async with factory() as db:
            yield db
    app.dependency_overrides[get_db] = database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://config.test') as http:
        yield SimpleNamespace(app=app, http=http, factory=factory, api=client, alice={'Authorization': 'Bearer '+create_access_token('alice')}, bob={'Authorization': 'Bearer '+create_access_token('bob')})
    await engine.dispose()


def payload(**changes):
    return dict(name='我的中转服务', provider='custom', protocol='openai', base_url='https://gateway.test/v1', api_key='synthetic-private-key', model='vendor/new-model', **changes)


async def save(c, **changes):
    body = {**payload(), **changes}
    response = await c.http.post('/api/v1/ai/connections', headers=c.alice, json=body)
    assert response.status_code == 201, response.text
    assert 'synthetic-private-key' not in response.text
    return response.json()


async def test_save_reopen_and_cold_adapter_use_exact_connection(connections, monkeypatch):
    c = connections
    saved = await save(c)
    ref = saved['model_ref']
    response = await c.http.put('/api/v1/ai/default', headers=c.alice, json={'connection_id': saved['id'], 'model': 'vendor/new-model'})
    assert response.status_code == 200, response.text
    config = (await c.http.get('/api/v1/ai/config', headers=c.alice)).json()
    row = config['connections'][0]
    assert (row['base_url'], row['protocol'], row['model'], row['name']) == ('https://gateway.test/v1', 'openai', 'vendor/new-model', '我的中转服务')
    assert config['default_model'] == ref
    assert 'synthetic-private-key' not in json.dumps(config)
    import app.core.api_scheduler as scheduler
    cold = UnifiedAPIClient(); monkeypatch.setattr(scheduler, 'api_client', cold)
    async with c.factory() as db:
        await load_user_api_keys('alice', db)
    adapter = cold.get_adapter(ref, user_id='alice')
    assert adapter.base_url == 'https://gateway.test/v1'
    assert adapter.api_key == 'synthetic-private-key'
    assert adapter.model_name == 'vendor/new-model'
    with pytest.raises(ValueError):
        cold.get_adapter(ref, user_id='bob')


async def test_edit_keeps_secret_but_new_destination_requires_reentry(connections):
    c = connections; row = await save(c)
    edited = {**payload(), 'api_key': '', 'name': '改名后的连接', 'model': 'vendor/model-2'}
    response = await c.http.put('/api/v1/ai/connections/'+row['id'], headers=c.alice, json=edited)
    assert response.status_code == 200, response.text
    changed = await c.http.put('/api/v1/ai/connections/'+row['id'], headers=c.alice, json={**edited, 'base_url': 'https://other.test/v1'})
    assert changed.status_code == 422
    async with c.factory() as db:
        await load_user_api_keys('alice', db)
    adapter = c.api.get_adapter(row['model_ref'].split('/')[0]+'/vendor/model-2', 'alice')
    assert adapter.api_key == 'synthetic-private-key'


async def test_accounts_and_disable_do_not_fall_back_to_other_keys(connections):
    c = connections; row = await save(c)
    assert (await c.http.get('/api/v1/ai/config', headers=c.bob)).json()['connections'] == []
    foreign = await c.http.delete('/api/v1/ai/connections/'+row['id'], headers=c.bob)
    assert foreign.status_code == 404
    c.api.configure_adapter('openai', 'global-fallback')
    response = await c.http.put('/api/v1/ai/connections/'+row['id'], headers=c.alice, json={**payload(), 'api_key':'', 'is_active': False})
    assert response.status_code == 200
    async with c.factory() as db:
        await load_user_api_keys('alice', db)
    with pytest.raises(ValueError): c.api.get_adapter(row['model_ref'], 'alice')


@pytest.mark.parametrize('url', ['file:///tmp/key', 'https://user:password@host.test/v1', 'https://host.test/v1?key=secret', 'https://host.test/v1#fragment', 'http://public.test/v1', 'https://host.test:notaport/v1', 'https://host.test:99999/v1'])
async def test_invalid_url_is_rejected_without_writes(connections, url):
    c = connections
    response = await c.http.post('/api/v1/ai/connections', headers=c.alice, json={**payload(), 'base_url': url})
    assert response.status_code == 422
    assert (await c.http.get('/api/v1/ai/config', headers=c.alice)).json()['connections'] == []


async def test_multiple_connections_of_same_provider_are_independent(connections):
    c = connections
    a = await save(c, provider='openai', name='网关A')
    b = await save(c, provider='openai', name='网关B', base_url='https://gateway-b.test/v1')
    assert a['id'] != b['id']
    assert len((await c.http.get('/api/v1/ai/config', headers=c.alice)).json()['connections']) == 2


async def test_validation_never_echoes_submitted_secrets(connections):
    c = connections
    secret = 'synthetic-secret-that-must-stay-private'
    response = await c.http.post('/api/v1/ai/connections', headers=c.alice,
        json={**payload(), 'base_url': 'https://host.test/v1?token='+secret, 'api_key': secret+'\ninvalid'})
    assert response.status_code == 422
    assert secret not in response.text
    assert all('input' not in issue and 'ctx' not in issue for issue in response.json()['detail'])


async def test_removing_default_retains_blocked_reference(connections):
    c = connections; row = await save(c)
    await c.http.put('/api/v1/ai/default', headers=c.alice, json={'connection_id': row['id'], 'model': row['model']})
    assert (await c.http.delete('/api/v1/ai/connections/'+row['id'], headers=c.alice)).status_code == 200
    c.api.configure_adapter('openai', 'synthetic-global-key')
    config = (await c.http.get('/api/v1/ai/config', headers=c.alice)).json()
    assert config['connections'] == [] and config['default_model'] == row['model_ref']
    with pytest.raises(ValueError): c.api.get_adapter(row['model_ref'], 'alice')


async def test_copy_preserves_original_and_legacy_api_excludes_connections(connections):
    from app.core.crypto import encrypt_api_key
    from app.api import user
    c = connections; c.app.include_router(user.router)
    async with c.factory() as db:
        db.add(APIKey(id='legacy', user_id='alice', provider='openai', key_alias='旧记录', key_encrypted=encrypt_api_key('synthetic-old-key')))
        await db.commit()
    response = await c.http.post('/api/v1/ai/connections/legacy/copy', headers=c.alice)
    assert response.status_code == 201, response.text
    assert 'synthetic-old-key' not in response.text
    keys = await c.http.get('/api/v1/user/keys', headers=c.alice)
    assert keys.status_code == 200, keys.text
    assert [key['id'] for key in keys.json()] == ['legacy']
    assert (await c.http.post('/api/v1/ai/connections/legacy/copy', headers=c.bob)).status_code == 404
    async with c.factory() as db:
        assert (await db.get(APIKey, 'legacy')).key_alias == '旧记录'


async def test_changed_configuration_rejects_stale_network_result(connections):
    from app.core import model_connections as service
    c = connections; row = await save(c)
    async with c.factory() as db:
        key, meta = await service.owned(db, row['id'], 'alice')
        stamp = service.network_stamp(key, meta)
        await db.rollback()
        response = await c.http.put('/api/v1/ai/connections/'+row['id'], headers=c.alice,
            json={**payload(), 'api_key': '', 'model': 'changed-model'})
        assert response.status_code == 200
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            await service.finish_network(db, row['id'], 'alice', stamp, {'last_test': {'status': 'success'}})
        assert error.value.status_code == 409
    config = (await c.http.get('/api/v1/ai/config', headers=c.alice)).json()
    assert config['connections'][0]['model'] == 'changed-model' and config['connections'][0]['last_test'] is None


async def test_probe_does_not_forward_credentials_on_redirect(connections):
    from fastapi.responses import RedirectResponse
    from tests.test_reading_native_stream_http_flow import server
    provider = FastAPI(); leaked = []
    @provider.post('/v1/chat/completions')
    async def redirect():
        return RedirectResponse('/sink', status_code=302)
    @provider.get('/sink')
    async def sink():
        leaked.append(True)
        return {'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'stop'}]}
    c = connections
    async with server(provider) as remote:
        row = await save(c, base_url=str(remote.base_url).rstrip('/')+'/v1')
        result = await c.http.post('/api/v1/ai/connections/'+row['id']+'/test', headers=c.alice)
        assert result.status_code == 200 and result.json()['status'] == 'failed'
        assert 'synthetic-private-key' not in result.text
        assert not leaked


async def test_signed_local_account_can_reload_own_connection(connections):
    c = connections
    local = {'Authorization': 'Bearer '+create_access_token('local_user')}
    response = await c.http.post('/api/v1/ai/connections', headers=local, json=payload())
    assert response.status_code == 201, response.text
    row = response.json()
    async with c.factory() as db:
        await load_user_api_keys('local_user', db, True)
    assert c.api.get_adapter(row['model_ref'], 'local_user').base_url == 'https://gateway.test/v1'


@pytest.mark.parametrize('protocol', ['openai', 'anthropic'])
async def test_actual_http_discovery_probe_and_cold_generation(connections, monkeypatch, protocol):
    from fastapi import Request
    from tests.test_pipeline_lifecycle_flow import localhost
    from app.core.api_scheduler.adapters.base import AdapterConfig
    provider = FastAPI(); seen = []
    @provider.get('/v1/models')
    async def models(request: Request):
        seen.append(('models', request.headers.get('authorization')))
        return {'data': [{'id': 'vendor/brand-new', 'name': 'Brand New Model'}]}
    @provider.post('/v1/chat/completions')
    @provider.post('/v1/messages')
    async def completion(request: Request):
        body = await request.json()
        seen.append((request.url.path, body['model'], request.headers.get('authorization'), request.headers.get('x-api-key')))
        if protocol == 'anthropic':
            return {'model': body['model'], 'content': [{'type': 'text', 'text': 'OK'}], 'stop_reason': 'end_turn', 'usage': {'input_tokens': 5, 'output_tokens': 1}}
        return {'model': body['model'], 'choices': [{'message': {'content': 'OK'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 5, 'completion_tokens': 1}}
    c = connections
    async with localhost(provider) as provider_http:
        base_url = str(provider_http.base_url).rstrip('/')+'/v1'
        row = await save(c, base_url=base_url, protocol=protocol, model='vendor/brand-new')
        response = await c.http.post('/api/v1/ai/connections/'+row['id']+'/models', headers=c.alice)
        assert response.status_code == 200, response.text
        assert response.json()['models'][0]['display_name'] == 'Brand New Model'
        result = await c.http.post('/api/v1/ai/connections/'+row['id']+'/test', headers=c.alice)
        assert result.status_code == 200 and result.json()['status'] == 'success', result.text
        chosen = await c.http.put('/api/v1/ai/default', headers=c.alice, json={'connection_id': row['id'], 'model': 'vendor/brand-new'})
        assert chosen.status_code == 200
        import app.core.api_scheduler as scheduler
        cold = UnifiedAPIClient(); monkeypatch.setattr(scheduler, 'api_client', cold)
        async with c.factory() as db:
            await load_user_api_keys('alice', db)
        reply = await cold.get_adapter(row['model_ref'], 'alice').chat_completion([{'role': 'user', 'content': 'synthetic cold-start probe'}], AdapterConfig(max_tokens=128, timeout=5))
        assert reply.content == 'OK'
        assert seen[0] == ('models', 'Bearer synthetic-private-key')
        assert seen[-1][0] == ('/v1/messages' if protocol == 'anthropic' else '/v1/chat/completions')
        assert seen[-1][1] == 'vendor/brand-new'
        if protocol == 'anthropic': assert seen[-1][3] == 'synthetic-private-key'


async def test_anonymous_requests_cannot_use_local_accounts_connection(connections):
    c = connections
    local = {'Authorization': 'Bearer '+create_access_token('local_user')}
    response = await c.http.post('/api/v1/ai/connections', headers=local, json=payload())
    row = response.json()
    async with c.factory() as db:
        await load_user_api_keys('local_user', db)
    with pytest.raises(ValueError): c.api.get_adapter(row['model_ref'])


async def test_existing_server_default_is_visible_until_personal_choice(connections):
    c = connections
    c.api.configure_adapter('openai', 'synthetic-server-key', model_name='server-model')
    c.api.set_default_configuration('server-model', [])
    config = (await c.http.get('/api/v1/ai/config', headers=c.alice)).json()
    assert config['default_model'] == 'openai/server-model' and config['default_source'] == 'legacy'
    row = await save(c)
    await c.http.put('/api/v1/ai/default', headers=c.alice, json={'connection_id': row['id'], 'model': row['model']})
    config = (await c.http.get('/api/v1/ai/config', headers=c.alice)).json()
    assert config['default_model'] == row['model_ref'] and config['default_source'] == 'personal'


async def test_default_reaches_omitted_request_and_upload_extraction_job(connections):
    from app.api.learning import GeneratePackRequest
    from app.core.model_connections import choose_request_model
    from app.core.knowledge_network import register_extraction
    from app.models import Document, DocumentChunk
    c = connections; row = await save(c)
    await c.http.put('/api/v1/ai/default', headers=c.alice, json={'connection_id': row['id'], 'model': row['model']})
    async with c.factory() as db:
        request = GeneratePackRequest(document_id='config-source')
        await choose_request_model(request, 'alice', db)
        assert request.model == row['model_ref']
        explicit = GeneratePackRequest(document_id='config-source', model='openai/manual-override')
        await choose_request_model(explicit, 'alice', db)
        assert explicit.model == 'openai/manual-override'
        db.add(Document(id='config-source', filename='biology.md', file_type='md', status='ready', sha256='synthetic-source-hash',
            metadata_={'knowledge_owner_id': 'alice'}))
        await db.flush()
        db.add(DocumentChunk(id='config-chunk', doc_id='config-source', chunk_index=0, text_content='细胞膜控制物质进出。'))
        await db.commit()
        job = await register_extraction(db, 'config-source', 'alice')
        assert job.model == row['model_ref']
