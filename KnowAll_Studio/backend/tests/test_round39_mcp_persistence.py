"""Real JWT/SQLite MCP persistence, with no external MCP processes or providers."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from app.database import get_db
from app.models.system_config import SystemConfig
from app.core import crypto
from tests.test_product_auth import account_app, register, bearer, load_account_router

mcp = load_account_router('mcp')
SERVER = {'name': '私有工具', 'command': 'never-execute', 'args': ['--fixture'],
          'env': {'API_KEY': 'synthetic-secret-39', 'LANG': 'zh_CN'}, 'enabled': True}


@pytest.fixture
async def private_app(account_app, tmp_path, monkeypatch):
    monkeypatch.setattr(crypto, '_KEY_FILE', tmp_path/'key')
    crypto._get_or_create_key()  # Existing stable deployment key for shared-worker tests.
    async with account_app.sessions() as db:
        async with db.bind.begin() as c:
            await c.run_sync(SystemConfig.__table__.create)
    for prefix in ('', '/api/v1'):
        account_app.app.include_router(mcp.router, prefix=prefix)
    return account_app


def second_app(fixture):
    app = FastAPI()
    for prefix in ('', '/api/v1'):
        app.include_router(load_account_router('mcp').router, prefix=prefix)
    app.dependency_overrides[get_db] = fixture.app.dependency_overrides[get_db]
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')


@pytest.mark.parametrize('prefix', ['/api/v1/mcp', '/mcp'])
async def test_second_worker_recovers_private_encrypted_configuration(private_app, prefix):
    alice = await register(private_app)
    bob = await register(private_app, username='other39', email='other39@example.com')
    ah, bh = bearer(alice['access_token']), bearer(bob['access_token'])
    response = await private_app.client.post(prefix+'/servers', headers=ah, json=SERVER)
    assert response.status_code == 200, response.text
    assert response.json()['server']['env']['API_KEY'] == '***'
    async with private_app.sessions() as db:
        rows = (await db.scalars(select(SystemConfig))).all()
        assert len(rows) == 1  # Old implementation stores only in a singleton.
        assert 'synthetic-secret-39' not in rows[0].value
    async with second_app(private_app) as worker:
        recovered = await worker.get(prefix+'/servers', headers=ah)
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()['servers'][0]['env'] == {'API_KEY': '***', 'LANG': 'zh_CN'}
        assert (await worker.get(prefix+'/servers', headers=bh)).json()['servers'] == []
        assert (await worker.delete(prefix+'/servers/'+SERVER['name'], headers=bh)).status_code == 200
        assert len((await worker.get(prefix+'/servers', headers=ah)).json()['servers']) == 1
        assert (await worker.delete(prefix+'/servers/'+SERVER['name'], headers=ah)).status_code == 200
    assert (await private_app.client.get(prefix+'/servers', headers=ah)).json()['servers'] == []


async def test_same_name_different_accounts_and_long_names_are_independent(private_app):
    alice = await register(private_app)
    bob = await register(private_app, username='other39', email='other39@example.com')
    ah, bh = bearer(alice['access_token']), bearer(bob['access_token'])
    name = '学'*100
    for headers, command in ((ah,'alice-never-execute'), (bh,'bob-never-execute')):
        assert (await private_app.client.post('/mcp/servers', headers=headers, json={**SERVER, 'name':name,'command':command})).status_code == 200
    for headers, command in ((ah,'alice-never-execute'),(bh,'bob-never-execute')):
        servers = (await private_app.client.get('/api/v1/mcp/servers',headers=headers)).json()['servers']
        assert len(servers) == 1 and servers[0]['command'] == command
    async with private_app.sessions() as db:
        rows = (await db.scalars(select(SystemConfig))).all()
        assert len(rows) == 2 and all(len(row.key) <= 128 for row in rows)


async def test_concurrent_distinct_and_same_name_updates_are_atomic(private_app):
    headers = bearer((await register(private_app))['access_token'])
    async with second_app(private_app) as other:
        responses = await asyncio.gather(*[
            (private_app.client if i%2 else other).post('/mcp/servers',headers=headers,json={**SERVER,'name':f'server-{i}'}) for i in range(6)])
        assert all(r.status_code == 200 for r in responses)
        assert len((await other.get('/mcp/servers',headers=headers)).json()['servers']) == 6
        responses = await asyncio.gather(*[other.post('/mcp/servers',headers=headers,json={**SERVER,'name':'shared','command':f'never-{i}'}) for i in range(6)])
        assert all(r.status_code == 200 for r in responses)
        entries = (await other.get('/mcp/servers',headers=headers)).json()['servers']
        assert len(entries) == 7 and len([v for v in entries if v['name']=='shared']) == 1


async def test_anonymous_configuration_operations_never_write(private_app):
    for prefix in ('/mcp', '/api/v1/mcp'):
        assert (await private_app.client.get(prefix+'/servers')).status_code == 401
        assert (await private_app.client.post(prefix+'/servers',json=SERVER)).status_code == 401
        assert (await private_app.client.delete(prefix+'/servers/x')).status_code == 401
    async with private_app.sessions() as db:
        assert (await db.scalars(select(SystemConfig))).all() == []


@pytest.mark.parametrize('corruption', ['invalid-cipher', 'bad-version', 'wrong-owner', 'wrong-name'])
async def test_corrupt_configuration_is_safe_failure_and_can_be_replaced(private_app, corruption):
    alice = await register(private_app)
    headers = bearer(alice['access_token'])
    assert (await private_app.client.post('/mcp/servers', headers=headers, json=SERVER)).status_code == 200
    async with private_app.sessions() as db:
        row = await db.scalar(select(SystemConfig))
        raw = json.loads(crypto.decrypt_api_key(row.value))
        if corruption == 'bad-version': raw['version'] = 99
        if corruption == 'wrong-owner': raw['owner'] = 'other-owner'
        if corruption == 'wrong-name': raw['config']['name'] = 'other-name'
        row.value = 'private-corrupt-data' if corruption == 'invalid-cipher' else crypto.encrypt_api_key(json.dumps(raw))
        await db.commit()
    failed = await private_app.client.get('/mcp/servers', headers=headers)
    assert failed.status_code == 503
    assert 'private-corrupt-data' not in failed.text and 'synthetic-secret-39' not in failed.text
    assert (await private_app.client.post('/mcp/servers', headers=headers, json=SERVER)).status_code == 200
    assert len((await private_app.client.get('/mcp/servers', headers=headers)).json()['servers']) == 1
    assert (await private_app.client.delete('/mcp/servers/'+SERVER['name'], headers=headers)).status_code == 200


async def test_private_transport_receives_original_secret_only_for_owner(private_app, monkeypatch):
    from app.core.mcp.client import MCPClient
    calls = []
    async def harmless_call(self, server, tool, arguments):
        calls.append((server.name, dict(server.env), tool, arguments))
        return {'fixture': True}
    monkeypatch.setattr(MCPClient, '_call_stdio', harmless_call)
    alice = await register(private_app)
    bob = await register(private_app, username='other39', email='other39@example.com')
    ah, bh = bearer(alice['access_token']), bearer(bob['access_token'])
    await private_app.client.post('/mcp/servers',headers=ah,json=SERVER)
    body = {'server_name':SERVER['name'],'tool_name':'fixture','arguments':{'number':1}}
    assert 'not found' in (await private_app.client.post('/api/v1/mcp/tools/call',headers=bh,json=body)).json()['result']['error']
    assert calls == []
    async with second_app(private_app) as other:
        response = await other.post('/api/v1/mcp/tools/call',headers=ah,json=body)
    assert response.json()['result'] == {'fixture':True}
    assert calls == [(SERVER['name'], SERVER['env'], 'fixture', {'number':1})]
    await private_app.client.post('/mcp/servers',headers=ah,json={**SERVER,'enabled':False})
    assert 'disabled' in (await private_app.client.post('/mcp/tools/call',headers=ah,json=body)).json()['result']['error']
    assert len(calls)==1


async def test_disabled_discovery_never_executes_transport(private_app, monkeypatch):
    from app.core.mcp.client import MCPClient
    calls=[]
    async def harmless_discovery(self, server):
        calls.append(server.name)
        return [{'name':'fixture'}]
    monkeypatch.setattr(MCPClient, '_list_tools_stdio', harmless_discovery)
    headers=bearer((await register(private_app))['access_token'])
    await private_app.client.post('/mcp/servers',headers=headers,json={**SERVER,'enabled':False})
    response=await private_app.client.get('/mcp/servers/'+SERVER['name']+'/tools',headers=headers)
    assert response.json()['tools']==[] and calls==[]
    await private_app.client.post('/mcp/servers',headers=headers,json=SERVER)
    assert (await private_app.client.get('/mcp/servers/'+SERVER['name']+'/tools',headers=headers)).json()['tools']==[{'name':'fixture'}]
    assert calls==[SERVER['name']]


async def test_delete_corrupt_row_without_decrypting(private_app):
    headers=bearer((await register(private_app))['access_token'])
    await private_app.client.post('/mcp/servers',headers=headers,json=SERVER)
    async with private_app.sessions() as db:
        row=await db.scalar(select(SystemConfig)); row.value='unreadable'; await db.commit()
    assert (await private_app.client.get('/mcp/servers',headers=headers)).status_code==503
    assert (await private_app.client.delete('/mcp/servers/'+SERVER['name'],headers=headers)).status_code==200
    assert (await private_app.client.get('/mcp/servers',headers=headers)).json()['servers']==[]


async def test_failed_commit_does_not_publish_configuration(private_app):
    headers=bearer((await register(private_app))['access_token'])
    original=private_app.app.dependency_overrides[get_db]
    async def failing_database():
        async with private_app.sessions() as db:
            async def failure(): raise RuntimeError('synthetic commit failure')
            db.commit=failure
            yield db
    private_app.app.dependency_overrides[get_db]=failing_database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=private_app.app,raise_app_exceptions=False),base_url='http://test') as client:
        response=await client.post('/mcp/servers',headers=headers,json=SERVER)
        assert response.status_code==500 and 'synthetic-secret' not in response.text
    private_app.app.dependency_overrides[get_db]=original
    assert (await private_app.client.get('/mcp/servers',headers=headers)).json()['servers']==[]


@pytest.mark.parametrize('dialect', ['mysql','postgresql'])
async def test_native_production_dialect_upsert_compiles(private_app, dialect):
    # Compile the real emitted statement, no production DB connection implied.
    from app.core.mcp.store import save_server
    from app.core.mcp.client import MCPServerConfig
    from sqlalchemy.dialects import mysql, postgresql
    statements=[]
    class CompileOnlySession:
        def get_bind(self): return SimpleNamespace(dialect=SimpleNamespace(name=dialect))
        async def execute(self, statement): statements.append(statement)
        async def commit(self): pass
    await save_server(CompileOnlySession(),'owner',MCPServerConfig(**SERVER))
    engine_dialect=mysql.dialect() if dialect=='mysql' else postgresql.dialect()
    compiled=str(statements[0].compile(dialect=engine_dialect))
    assert ('ON DUPLICATE KEY UPDATE' if dialect=='mysql' else 'ON CONFLICT (key) DO UPDATE') in compiled
