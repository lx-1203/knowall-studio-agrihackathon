"""Cloud gateway security and streaming, using isolated account data and ASGI workers."""
import asyncio
import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


@pytest.fixture(scope='session')
def password_hash():
    from app.core.auth import hash_password
    return hash_password('password123')


@pytest.fixture
async def gateway(tmp_path, password_hash):
    assert importlib.util.find_spec('app.core.cloud_gateway') is not None, 'cloud gateway is missing'
    module = importlib.import_module('app.core.cloud_gateway')
    from app.database import Base, get_db
    from app.models import User
    from app.models.user_bind import UserBind
    from app.core.auth import create_access_token
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'accounts.sqlite').as_posix())
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: Base.metadata.create_all(conn, tables=[User.__table__, UserBind.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        session.add_all([User(id=uid, username=uid, email=uid + '@example.com',
                            password_hash=password_hash, is_active=uid != 'disabled')
                         for uid in ('alice', 'bob', 'disabled')])
        await session.commit()

    async def identity_lookup(uid):
        async with sessions() as session:
            return await session.get(User, uid)

    async def db_dependency():
        async with sessions() as session:
            yield session

    workers = FastAPI()
    seen = []
    leases = []
    active = SimpleNamespace(count=0)

    @workers.api_route('/{path:path}', methods=['GET', 'POST', 'DELETE'])
    async def echo(request: Request, path: str):
        body = await request.body()
        seen.append((request.url.port, path, request.headers, body))
        if path == 'api/v1/redirect':
            return Response(status_code=302, headers={'location': 'https://external.invalid/steal'})
        return Response(body or str(request.url.port).encode(), headers={'connection': 'x-private',
                        'x-private': 'hidden', 'x-result': 'kept', 'x-query': request.url.query}, media_type='application/octet-stream')

    class Pool:
        @asynccontextmanager
        async def lease(self, user):
            leases.append(user.id)
            active.count += 1
            try:
                yield SimpleNamespace(origin='http://127.0.0.1:' + ('8101' if user.id == 'alice' else '8102'))
            finally:
                active.count -= 1

        async def close(self):
            pass

    proxy = httpx.AsyncClient(transport=httpx.ASGITransport(app=workers), trust_env=False)
    app = module.create_cloud_app(Pool(), identity_lookup=identity_lookup, http_client=proxy)
    app.dependency_overrides[get_db] = db_dependency
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://gateway') as client:
        yield SimpleNamespace(app=app, client=client, seen=seen, leases=leases, active=active,
                              auth=lambda uid: {'authorization': 'Bearer ' + create_access_token(uid)})
    await proxy.aclose()
    await engine.dispose()


@pytest.mark.parametrize('path', ['/api/v1/documents', '/api/v1/auth/unknown', '/api/unknown', '/ws/unknown'])
async def test_private_and_unknown_routes_reject_anonymous(gateway, path):
    assert (await gateway.client.get(path)).status_code == 401
    assert not gateway.leases


@pytest.mark.parametrize('uid', ['missing', 'disabled'])
async def test_tokens_require_an_active_central_account(gateway, uid):
    assert (await gateway.client.get('/api/v1/documents', headers=gateway.auth(uid))).status_code == 401
    assert not gateway.leases


async def test_account_login_and_profile_stay_central(gateway):
    response = await gateway.client.post('/api/v1/auth/login', json={'username': 'alice', 'password': 'password123'})
    assert response.status_code == 200
    profile = await gateway.client.get('/api/v1/user/profile', headers=gateway.auth('alice'))
    assert profile.json()['id'] == 'alice'
    binds = await gateway.client.get('/api/v1/user/binds', headers=gateway.auth('alice'))
    assert binds.status_code == 200 and len(binds.json()) == 4
    assert not gateway.leases


async def test_devices_share_workspace_while_different_accounts_are_isolated(gateway):
    for uid in ['alice', 'alice', 'bob']:
        response = await gateway.client.get('/api/v1/documents', headers=gateway.auth(uid))
        assert response.content == (b'8101' if uid == 'alice' else b'8102')
    assert gateway.leases == ['alice', 'alice', 'bob']


@pytest.mark.parametrize('path', ['/api/v1/user/keys', '/api/v1/user/history'])
async def test_keys_and_learning_history_are_in_personal_workspace(gateway, path):
    assert (await gateway.client.get(path, headers=gateway.auth('alice'))).content == b'8101'
    assert gateway.leases == ['alice']


async def test_large_upload_and_headers_survive_proxy(gateway):
    content = b'large chunk\x00' * 100000
    headers = gateway.auth('alice') | {'connection': 'x-private', 'x-private': 'hidden', 'x-custom': 'kept'}
    response = await gateway.client.post('/api/v1/documents?tag=a&tag=b', content=content, headers=headers)
    assert response.content == content
    assert response.headers['x-query'] == 'tag=a&tag=b'
    assert response.headers['x-result'] == 'kept' and 'x-private' not in response.headers
    _, _, upstream_headers, upstream_body = gateway.seen[-1]
    assert upstream_body == content and upstream_headers['authorization'] == headers['authorization']
    assert upstream_headers['x-custom'] == 'kept' and 'x-private' not in upstream_headers


async def test_upstream_redirect_never_follows_external_host(gateway):
    response = await gateway.client.get('/api/v1/redirect', headers=gateway.auth('alice'))
    assert response.status_code == 502
    assert len(gateway.seen) == 1


async def test_cloud_local_session_is_always_disabled(gateway):
    response = await gateway.client.post('/api/v1/auth/local-session', json={})
    assert response.status_code == 200 and response.json() == {'enabled': False}
    assert not gateway.leases


def test_cloud_entry_bootstraps_persistent_configuration_without_developer_env(tmp_path):
    entry = Path(__file__).resolve().parents[1] / 'cloud_entry.py'
    assert entry.exists(), 'cloud entry is missing'
    env = os.environ.copy()
    env.pop('JWT_SECRET', None)
    env.update(KNOWALL_CLOUD_DATA_ROOT=str(tmp_path), DEBUG='true', LOCAL_AUTO_LOGIN='true')
    code = "import cloud_entry,os,json; print(json.dumps({k:os.environ[k] for k in ['DATABASE_URL','JWT_SECRET','DEBUG','LOCAL_AUTO_LOGIN','KNOWALL_IGNORE_ENV_FILE']}))"
    results = []
    for _ in range(2):
        run = subprocess.run([sys.executable, '-c', code], cwd=entry.parent, env=env, capture_output=True, text=True, check=True)
        results.append(json.loads(run.stdout.splitlines()[-1]))
    assert results[0]['JWT_SECRET'] == results[1]['JWT_SECRET'] and len(results[0]['JWT_SECRET']) >= 32
    assert results[0]['DATABASE_URL'] == 'sqlite+aiosqlite:///' + (tmp_path / 'accounts.sqlite').as_posix()
    assert results[0]['DEBUG'] == 'false' and results[0]['LOCAL_AUTO_LOGIN'] == 'false'
    assert results[0]['KNOWALL_IGNORE_ENV_FILE'] == 'true'
    assert not (tmp_path / 'accounts.sqlite').exists(), 'import must not initialize account data'


async def websocket_events(app, path, authorization=None, query_token=None):
    incoming = asyncio.Queue()
    await incoming.put({'type': 'websocket.connect'})
    events = []
    scope = {'type': 'websocket', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
             'scheme': 'ws', 'path': path, 'raw_path': path.encode(),
             'query_string': ('token=' + query_token).encode() if query_token else b'',
             'headers': [(b'authorization', authorization.encode())] if authorization else [],
             'client': ('127.0.0.1', 40000), 'server': ('gateway', 80), 'subprotocols': []}

    async def send(event):
        events.append(event)
    await asyncio.wait_for(app(scope, incoming.get, send), timeout=5)
    return events


async def test_websocket_requires_active_central_account(gateway):
    for authorization in (None, gateway.auth('disabled')['authorization'], gateway.auth('missing')['authorization']):
        events = await websocket_events(gateway.app, '/ws/sync', authorization)
        assert events == [{'type': 'websocket.close', 'code': 4401}]
    assert not gateway.leases


async def test_unknown_websocket_never_launches_worker(gateway):
    events = await websocket_events(gateway.app, '/ws/unknown', gateway.auth('alice')['authorization'])
    assert events[0]['type'] == 'websocket.close' and not gateway.leases


@pytest.mark.parametrize('token_source', ['header', 'query'])
async def test_websocket_token_is_forwarded_to_same_personal_worker(gateway, monkeypatch, token_source):
    from contextlib import asynccontextmanager
    seen = []
    class WorkerSocket:
        def __aiter__(self):
            return self
        async def __anext__(self):
            raise StopAsyncIteration

    @asynccontextmanager
    async def connect(target, **kwargs):
        seen.append((target, kwargs))
        yield WorkerSocket()

    monkeypatch.setattr('app.core.cloud_gateway.connect_worker_websocket', connect)
    authorization = gateway.auth('alice')['authorization']
    events = await websocket_events(gateway.app, '/ws/sync', authorization if token_source == 'header' else None,
                                    query_token=authorization[7:] if token_source == 'query' else None)
    assert events[0]['type'] == 'websocket.accept'
    assert seen[0][0].startswith('ws://127.0.0.1:8101/ws/sync?token=')
    assert seen[0][1]['additional_headers']['Authorization'] == authorization
    assert seen[0][1]['proxy'] is None and gateway.leases == ['alice']


async def test_worker_websocket_handshake_failure_closes_cleanly(gateway, monkeypatch):
    from contextlib import asynccontextmanager
    from websockets.exceptions import InvalidHandshake

    @asynccontextmanager
    async def connect(*args, **kwargs):
        raise InvalidHandshake('worker refused connection')
        yield

    monkeypatch.setattr('app.core.cloud_gateway.connect_worker_websocket', connect)
    events = await websocket_events(gateway.app, '/ws/sync', gateway.auth('alice')['authorization'])
    assert events[-1] == {'type': 'websocket.close', 'code': 1011, 'reason': ''}


@pytest.mark.parametrize('path', ['/api/v1/auth', '/api/v1/user/profile/', '/api/v1/user/password/',
                                 '/api/v1/oauth/github/callback', '/api/v1/sms/login'])
async def test_account_operations_never_fall_through_to_worker(gateway, path):
    response = await gateway.client.get(path, headers=gateway.auth('alice'))
    assert response.status_code == 404 and not gateway.leases


async def test_worker_lease_stays_active_during_sse_stream_and_is_released(gateway):
    observed = []
    class SSE(httpx.AsyncByteStream):
        async def __aiter__(self):
            for value in [b'data: first\n\n', b'data: second\n\n']:
                observed.append(gateway.active.count)
                yield value

    async def handler(request):
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=SSE())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as proxy:
        gateway.app.state.proxy_client = proxy
        response = await gateway.client.get('/api/v1/chat/stream', headers=gateway.auth('alice'))
    assert response.content == b'data: first\n\ndata: second\n\n'
    assert observed == [1, 1] and gateway.active.count == 0


@pytest.mark.parametrize('path', ['/glossary_terms.kgb'])
async def test_private_dictionary_snapshots_use_personal_workspace(gateway, path):
    assert (await gateway.client.get(path)).status_code == 401
    response = await gateway.client.get(path, headers=gateway.auth('alice'))
    assert response.status_code == 200 and response.content == b'8101'
    assert gateway.leases == ['alice']


async def test_websocket_connector_never_follows_redirect_with_account_token():
    from app.core import cloud_gateway
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response as WSResponse
    from websockets.datastructures import Headers
    assert hasattr(cloud_gateway, 'connect_worker_websocket'), 'redirect-safe websocket connector is missing'
    connection = cloud_gateway.connect_worker_websocket('ws://127.0.0.1:8101/ws/sync?token=private')
    redirect = InvalidStatus(WSResponse(302, 'Found', Headers({'location': 'wss://external.invalid/steal'})))
    assert connection.process_redirect(redirect) is redirect
