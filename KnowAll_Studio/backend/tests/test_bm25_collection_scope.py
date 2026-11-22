"""Collection scope: mocks + real temporary Chroma + TCP/SQLite/child writer.

Embeddings are explicit simulations. No model/download/provider requests.
The production 60s TTL is preserved; expiry is advanced in memory, no sleep.
"""
import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import chromadb
import httpx
import pytest
import uvicorn
from chromadb.config import Settings
from fastapi import FastAPI
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core import rag
from app.api import search as routes
from app.database import Base
from app.models import Document
from tests.test_hybrid_retrieval import _FakeCollection
from tests.test_search_citation_flow import SimulatedEmbedding


@pytest.fixture(autouse=True)
def clean_cache():
    rag._bm25_cache.clear()
    yield
    rag._bm25_cache.clear()


@pytest.mark.parametrize('identity', ['missing', 'uuid'])
def test_different_collections_same_name_ids_count_are_not_reused(identity):
    first = _FakeCollection('scope', {'same': ('old energy', {'page_range': '1'})})
    second = _FakeCollection('scope', {'same': ('new energy', {'page_range': '8'})})
    if identity == 'uuid':
        first.id, second.id = 'old-uuid', 'new-uuid'
    old = rag._bm25_index(first)
    new = rag._bm25_index(second)
    assert new is not old
    assert new.docs == ['new energy']
    assert new.metas == [{'page_range': '8'}]


def test_same_uuid_wrappers_reuse_index_without_full_corpus_read():
    first = _FakeCollection('scope', {'same': ('energy', {})})
    second = _FakeCollection('scope', {'same': ('energy', {})})
    first.id = second.id = 'same-uuid'
    old = rag._bm25_index(first)
    calls = []
    original = second.get
    def get(include=None):
        calls.append(include)
        return original(include)
    second.get = get
    assert rag._bm25_index(second) is old
    assert calls == [[]]


def test_identity_switch_read_failure_is_503_and_retry_rebuilds():
    first = _FakeCollection('scope', {'same': ('old energy', {})})
    second = _FakeCollection('scope', {'same': ('new energy', {})})
    first.id, second.id = 'first', 'second'
    rag._bm25_index(first)
    original = second.get
    second.get = lambda **kwargs: (_ for _ in ()).throw(OSError('controlled storage failure'))
    with pytest.raises(rag.RetrievalUnavailableError):
        rag._bm25_index(second, strict=True)
    second.get = original
    assert rag._bm25_index(second, strict=True).docs == ['new energy']


@pytest.mark.parametrize('age', [59.9, 60.0, 61.0])
def test_ttl_keeps_existing_bounded_consistency_contract(monkeypatch, age):
    now = [1000.0]
    monkeypatch.setattr(rag.time, 'monotonic', lambda: now[0])
    collection = _FakeCollection('scope', {'same': ('old energy', {'page_range': '1'})})
    old = rag._bm25_index(collection)
    collection._rows['same'] = ('new energy', {'page_range': '8'})
    now[0] += age
    new = rag._bm25_index(collection)
    assert (new is old) == (age < 60)
    assert new.docs == (['old energy'] if age < 60 else ['new energy'])


@pytest.mark.parametrize('same_root', [False, True])
def test_real_collection_identity_with_identical_name_and_chunks(tmp_path, same_root):
    client = chromadb.PersistentClient(path=str(tmp_path/'a'), settings=Settings(anonymized_telemetry=False))
    first = client.get_or_create_collection('scope-real')
    first.upsert(ids=['same'], documents=['old energy'], metadatas=[{'page_range': '1'}], embeddings=[[1., 0.]])
    old = rag._bm25_index(first)
    if same_root:
        client.delete_collection('scope-real')
        other = client
    else:
        other = chromadb.PersistentClient(path=str(tmp_path/'b'), settings=Settings(anonymized_telemetry=False))
    second = other.get_or_create_collection('scope-real')
    second.upsert(ids=['same'], documents=['new energy'], metadatas=[{'page_range': '8'}], embeddings=[[1., 0.]])
    assert first.id != second.id
    new = rag._bm25_index(second, strict=True)
    assert new.docs == ['new energy']
    assert new.metas == [{'page_range': '8'}]
    assert new is not old


@pytest.mark.parametrize('empty', [False, True])
def test_real_same_collection_wrappers_keep_cached_index(tmp_path, empty):
    client = chromadb.PersistentClient(path=str(tmp_path/'wrappers'), settings=Settings(anonymized_telemetry=False))
    first = client.get_or_create_collection('scope-wrapper')
    if not empty:
        first.upsert(ids=['same'], documents=['energy'], embeddings=[[1., 0.]])
    old = rag._bm25_index(first)
    second = client.get_collection('scope-wrapper')
    assert first is not second and first.id == second.id
    assert rag._bm25_index(second) is old
    assert old.docs == ([] if empty else ['energy'])


async def test_real_sql_tcp_scope_and_independent_process_refresh(tmp_path, monkeypatch, record_property):
    database_url = 'sqlite+aiosqlite:///'+(tmp_path/'source.sqlite').as_posix()
    monkeypatch.setattr(rag.settings, 'database_url', database_url)
    monkeypatch.setattr(rag.settings, 'hybrid_retrieval_enabled', True)
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[Document.__table__])
    async with async_sessionmaker(engine)() as db:
        db.add(Document(id='public', filename='scope.txt', file_type='txt', sha256='fixture'))
        await db.commit()
    clients, collections = [], []
    for name, text, page in [('a', 'energy from store A', '1'), ('b', 'energy from store B', '8')]:
        client = chromadb.PersistentClient(path=str(tmp_path/name), settings=Settings(anonymized_telemetry=False))
        col = client.get_or_create_collection('document_knowledge', embedding_function=SimulatedEmbedding())
        col.upsert(ids=['same'], documents=[text], metadatas=[{'doc_id': 'public', 'page_range': page}], embeddings=[[1., .1, .1]])
        clients.append(client)
        collections.append(col)
    active = [collections[0]]
    monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **k: active[0])
    app = FastAPI()
    app.include_router(routes.router)
    requests = []
    @app.middleware('http')
    async def observe(request, call_next):
        response = await call_next(request)
        requests.append({'path': request.url.path, 'status': response.status_code})
        return response
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(16)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic()+10
    while not server.started and time.monotonic() < deadline:
        await asyncio.sleep(.01)
    writer = None
    try:
        assert server.started
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=15) as http:
            async def context():
                response = await http.post('/api/v1/search/rag', json={'query': 'energy', 'top_k': 1})
                assert response.status_code == 200, response.text
                return response.json()
            assert (await context())['sources'][0]['metadata']['page_range'] == '1'
            active[0] = collections[1]
            b = await context()
            assert b['context'] == 'energy from store B'
            assert b['sources'][0]['metadata']['page_range'] == '8'
            # Explicit subprocess writes persisted Chroma, no product invalidation call.
            code = """import json, os, sys, chromadb
from chromadb.config import Settings
c=chromadb.PersistentClient(path=sys.argv[1],settings=Settings(anonymized_telemetry=False)).get_collection('document_knowledge')
c.upsert(ids=['same'],documents=['energy from writer C'],metadatas=[{'doc_id':'public','page_range':'9'}],embeddings=[[1.,.1,.1]])
print(json.dumps({'pid':os.getpid(),'ids':c.get(ids=['same'])['ids'],'text':c.get(ids=['same'])['documents']}))
"""
            writer = await asyncio.to_thread(subprocess.run, [sys.executable, '-c', code, str(tmp_path/'b')], capture_output=True, text=True, encoding='utf-8', timeout=30)
            assert writer.returncode == 0, writer.stderr
            receipt = json.loads(writer.stdout.strip().splitlines()[-1])
            assert receipt['pid'] != os.getpid()
            assert receipt['text'] == ['energy from writer C']
            fresh = await http.post('/api/v1/search/', json={'query': 'energy', 'top_k': 1})
            assert fresh.status_code == 200
            assert fresh.json()['results'][0]['text'] == 'energy from writer C'
            # Before expiry BM25 is deliberately bounded stale; after expiry it reloads.
            assert rag._bm25_index(collections[1]).docs == ['energy from store B']
            rag._bm25_cache['document_knowledge']['built_at'] -= 60
            renewed = await context()
            assert renewed['context'] == 'energy from writer C'
            source = renewed['sources'][0]
            assert source['id'] == 'same' and source['metadata'] == {'doc_id': 'public', 'page_range': '9'}
            assert rag._bm25_index(collections[1]).docs == ['energy from writer C']
            # SQL remains the authority; deleting public source excludes cached vectors.
            async with async_sessionmaker(engine)() as db:
                # Only ownership filtering is under test, not document API cascades.
                await db.execute(delete(Document).where(Document.id == 'public'))
                await db.commit()
            assert (await context())['sources'] == []
        assert len(requests) == 5 and all(r['status'] == 200 for r in requests)
    finally:
        server.should_exit = True
        await asyncio.to_thread(thread.join, 10)
        sock.close()
        await engine.dispose()
        assert not thread.is_alive()
        record_property('native', json.dumps({'requests': requests, 'writer': None if writer is None or writer.returncode else json.loads(writer.stdout.strip().splitlines()[-1]), 'readerPid': os.getpid(), 'port': port, 'tempRoot': str(tmp_path), 'serverStopped': not thread.is_alive(), 'providerRequests': 0, 'ttl': 'memory timestamp advanced by 60, no wall-clock wait', 'sourceSHA': hashlib.sha256(Path(rag.__file__).read_bytes()).hexdigest()}))
