"""Gateway isolation against real learning routes and separate private databases."""
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import documents, learning
from app.config import settings
from app.core import study_pack_service
from app.core.auth import create_access_token, decode_access_token, get_current_user
from app.core.cloud_gateway import create_cloud_app
from app.core.workspace_guard import WorkspaceOwnerGuard
from app.database import Base, get_db


@pytest.fixture
async def private_cloud(tmp_path, monkeypatch):
    from app.core.glossary import extractor
    monkeypatch.setattr(settings, 'document_dir', str(tmp_path / 'uploads'))
    monkeypatch.setattr(settings, 'kaf_publish_enabled', False)
    monkeypatch.setattr(settings, 'use_docling', False)
    monkeypatch.setattr(extractor, 'sync_glossary_from_db', AsyncMock())
    monkeypatch.setattr(learning, 'load_user_api_keys', AsyncMock())
    # Production separates settings by process. Context-local settings emulate
    # that separation while ASGI workers share this test interpreter.
    active_owner = ContextVar('test_workspace_owner')
    class PrivateSettings:
        def __getattr__(self, name):
            if name == 'document_dir':
                return str(tmp_path / active_owner.get() / 'documents')
            return getattr(settings, name)
    monkeypatch.setattr(documents, 'settings', PrivateSettings())

    async def generated(section, question_count, card_count, config):
        return {'notes': 'Private biology notes.', 'questions': [
            {'question_type': 'single_choice', 'question_text': 'Which organelle produces energy?',
             'options': [{'label': 'A', 'text': 'Mitochondria'}, {'label': 'B', 'text': 'Cell wall'}],
             'answer': 'A', 'analysis': 'Private source.'} for _ in range(question_count)], 'flashcards': []}
    monkeypatch.setattr(study_pack_service, 'generate_section', generated)

    async with AsyncExitStack() as stack:
        transports = {}
        workers = {}
        for owner, port in [('alice', 8101), ('bob', 8102)]:
            engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / f'{owner}.sqlite').as_posix())
            stack.push_async_callback(engine.dispose)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            def make_database(session_factory):
                async def database():
                    async with session_factory() as session:
                        yield session
                return database
            async def identity(request: Request):
                token = request.headers['authorization'][7:]
                return SimpleNamespace(id=decode_access_token(token))
            worker = FastAPI()
            worker.include_router(documents.router)
            worker.include_router(learning.router)
            worker.dependency_overrides[get_db] = make_database(factory)
            worker.dependency_overrides[get_current_user] = identity
            guard = WorkspaceOwnerGuard(worker, owner, decode_access_token)
            def scoped_worker(guard, owner):
                async def application(scope, receive, send):
                    marker = active_owner.set(owner)
                    try:
                        await guard(scope, receive, send)
                    finally:
                        active_owner.reset(marker)
                return application
            guarded = scoped_worker(guard, owner)
            workers[owner] = guarded
            transports[port] = httpx.ASGITransport(app=guarded)

        class RouterTransport(httpx.AsyncBaseTransport):
            async def handle_async_request(self, request):
                return await asyncio.wait_for(transports[request.url.port].handle_async_request(request), timeout=10)

        class Pool:
            @asynccontextmanager
            async def lease(self, user):
                yield SimpleNamespace(origin='http://127.0.0.1:' + ('8101' if user.id == 'alice' else '8102'))
            async def close(self):
                pass

        async def lookup(uid):
            return SimpleNamespace(id=uid, is_active=True) if uid in workers else None
        proxy = await stack.enter_async_context(httpx.AsyncClient(transport=RouterTransport(), trust_env=False))
        gateway = create_cloud_app(Pool(), identity_lookup=lookup, http_client=proxy)
        client = await stack.enter_async_context(httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway), base_url='http://cloud'))
        yield SimpleNamespace(client=client, workers=workers, root=tmp_path,
                              auth=lambda owner: {'Authorization': 'Bearer ' + create_access_token(owner)})


async def test_uploaded_document_and_pack_are_private_across_gateway_clients(private_cloud):
    cloud = private_cloud
    alice, bob = cloud.auth('alice'), cloud.auth('bob')
    payload = b'Mitochondria produce energy through cellular respiration in living cells.'
    upload = await cloud.client.post('/api/v1/documents/upload', headers=alice, files={'file': ('biology.txt', payload)})
    assert upload.status_code == 200, upload.text
    doc_id = upload.json()['document_id']
    # A second connection with the same account sees persisted state.
    async with httpx.AsyncClient(transport=cloud.client._transport, base_url='http://cloud') as second:
        own = await second.get('/api/v1/documents/', headers=alice)
        assert doc_id in {doc['id'] for doc in own.json()}
    assert (await cloud.client.get('/api/v1/documents/', headers=bob)).json() == []
    config = {'document_id': doc_id, 'question_count': 1, 'card_count': 0, 'question_types': ['single_choice']}
    generation = await cloud.client.post('/api/v1/learning/generate', headers=alice, json=config)
    assert generation.status_code == 200, generation.text
    pack = generation.json()
    assert pack['coverage']['failed_sections'] == 0
    for path in [f'/api/v1/documents/{doc_id}/chunks', f"/api/v1/learning/packs/{pack['id']}/export"]:
        denied = await cloud.client.get(path, headers=bob)
        assert denied.status_code == 404, denied.text
    deleted = await cloud.client.delete(f'/api/v1/documents/{doc_id}', headers=bob)
    assert deleted.status_code == 404
    session = await cloud.client.post(f"/api/v1/learning/packs/{pack['id']}/session", headers=bob, json={'mode': 'practice'})
    assert session.status_code == 404
    question_id = pack['sections'][0]['questions'][0]['id']
    answer = await cloud.client.post(f"/api/v1/learning/packs/{pack['id']}/answers", headers=bob,
                                   json={'question_id': question_id, 'answer': 'A'})
    assert answer.status_code == 404
    assert (await cloud.client.get(f'/api/v1/documents/{doc_id}/chunks', headers=alice)).status_code == 200
    bob_upload = await cloud.client.post('/api/v1/documents/upload', headers=bob, files={'file': ('biology.txt', payload)})
    assert bob_upload.status_code == 200, bob_upload.text
    assert bob_upload.json()['document_id'] != doc_id
    alice_files = list((cloud.root / 'alice' / 'documents').rglob('*'))
    bob_files = list((cloud.root / 'bob' / 'documents').rglob('*'))
    assert any(file.is_file() and file.read_bytes() == payload for file in alice_files)
    assert any(file.is_file() and file.read_bytes() == payload for file in bob_files)
    assert (await cloud.client.get(f'/api/v1/learning/documents/{doc_id}/pack', headers=bob)).json() is None


@pytest.mark.parametrize('kind', ['http', 'websocket'])
async def test_direct_private_worker_rejects_another_valid_account(private_cloud, kind):
    messages = []
    async def send(message):
        messages.append(message)
    async def receive():
        return {'type': 'http.disconnect'}
    token = private_cloud.auth('bob')['Authorization'].encode()
    scope = {'type': kind, 'path': '/api/v1/documents/upload', 'method': 'POST',
             'headers': [(b'authorization', token)], 'query_string': b''}
    await private_cloud.workers['alice'](scope, receive, send)
    assert messages[0].get('status', messages[0].get('code')) in {401, 4401}
    assert (await private_cloud.client.get('/api/v1/documents/', headers=private_cloud.auth('alice'))).json() == []
