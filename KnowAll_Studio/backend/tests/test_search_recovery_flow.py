"""Real temporary SQLite/Chroma; deterministic simulated embeddings, no model API."""
import asyncio
import json
import os
import socket
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.api import search as routes
from app.core import rag
from app.core.rag_pipeline.providers.chromadb_pipeline import ChromaDBPipeline
from app.core.tools.knowledge_search import KnowledgeSearchTool
from app.models import DocumentChunk
from app.database import get_db
from tests.test_search_citation_flow import store  # noqa: F401


class BrokenQuery:
    def query(self, **kwargs):
        raise OSError('private/storage/secret.sqlite cannot read')


@pytest.mark.parametrize('endpoint,hybrid', [('/', False), ('/rag', False), ('/rag', True)])
@pytest.mark.parametrize('failure', ['missing', 'open', 'query'])
async def test_unavailable_is_503_not_successful_empty(store, monkeypatch, endpoint, hybrid, failure):
    monkeypatch.setattr(rag.settings, 'hybrid_retrieval_enabled', hybrid)
    def collection(*args, **kwargs):
        if failure == 'open':
            raise OSError('private/storage/secret.sqlite cannot open')
        return None if failure == 'missing' else BrokenQuery()
    monkeypatch.setattr(rag, 'get_or_create_collection', collection)
    response = await store.http.post('/api/v1/search' + endpoint, json={'query': 'biology'})
    assert response.status_code == 503, response.text
    assert '重试' in response.json()['detail']
    assert 'secret.sqlite' not in response.text


@pytest.mark.parametrize('endpoint,hybrid', [('/', False), ('/rag', False), ('/rag', True)])
async def test_successful_empty_is_200(store, monkeypatch, endpoint, hybrid):
    monkeypatch.setattr(rag.settings, 'hybrid_retrieval_enabled', hybrid)
    response = await store.http.post('/api/v1/search' + endpoint, json={'query': 'biology'})
    assert response.status_code == 200, response.text
    if endpoint == '/':
        assert response.json()['results'] == []
        assert response.json()['count'] == 0
    else:
        assert response.json()['has_results'] is False
        assert response.json()['sources'] == []


async def test_legacy_fallback_and_strict_core(store, monkeypatch):
    monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: None)
    assert rag.search('biology') == []
    assert rag.rag_query('biology') == ''
    with pytest.raises(RuntimeError, match='重试'):
        rag.search('biology', strict=True)


async def test_unavailable_tool_is_not_successful_empty(store, monkeypatch):
    monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: None)
    tool = await KnowledgeSearchTool().execute(query='biology')
    assert tool.success is False
    assert '重试' in tool.error


async def test_unavailable_provider_is_not_successful_empty(store, monkeypatch):
    monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: None)
    with pytest.raises(RuntimeError, match='重试'):
        await ChromaDBPipeline().search('biology')


@pytest.mark.parametrize('operation', ['count', 'get'])
async def test_hybrid_keyword_storage_failure_is_503(store, monkeypatch, operation):
    class BrokenKeywords:
        def __getattr__(self, name):
            if name == operation:
                def fail(*a, **kw):
                    raise OSError('private/storage/secret.sqlite keywords failed')
                return fail
            return getattr(store.collection, name)
    monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: BrokenKeywords())
    monkeypatch.setattr(rag.settings, 'hybrid_retrieval_enabled', True)
    response = await store.http.post('/api/v1/search/rag', json={'query': 'biology'})
    assert response.status_code == 503, response.text
    assert 'secret.sqlite' not in response.text


async def test_service_recovers_with_real_sources(store, monkeypatch):
    monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: None)
    response = await store.http.post('/api/v1/search/', json={'query': '线粒体'})
    assert response.status_code == 503
    monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: store.collection)
    assert (await store.http.post('/api/v1/search/index', params={'doc_id': 'doc'})).status_code == 200
    response = await store.http.post('/api/v1/search/rag', json={'query': '线粒体'})
    assert response.status_code == 200
    assert response.json()['sources'][0]['metadata']['doc_id'] == 'doc'
    tool = await KnowledgeSearchTool().execute(query='线粒体')
    assert tool.success is True
    assert tool.output['results'][0]['id'] == 'chunk'
    provider = await ChromaDBPipeline().search('线粒体')
    assert provider.results[0].metadata['doc_id'] == 'doc'
    assert provider.results[0].id == 'chunk'


async def marker(store):
    async with store.factory() as db:
        return (await db.get(DocumentChunk, 'chunk')).vector_id


async def test_sql_marker_failure_can_retry_without_duplicate_vectors(store):
    attempts = []
    def fail_once(session):
        if not attempts and any(isinstance(row, DocumentChunk) and row.vector_id for row in session.dirty):
            attempts.append('commit failed')
            raise OSError('private/storage/secret.sqlite disk full')
    event.listen(Session, 'before_commit', fail_once)
    try:
        response = await store.http.post('/api/v1/search/index', params={'doc_id': 'doc'})
    finally:
        event.remove(Session, 'before_commit', fail_once)
    assert attempts == ['commit failed']
    assert response.status_code == 503, response.text
    assert '重试' in response.json()['detail']
    assert 'secret.sqlite' not in response.text
    assert await marker(store) is None
    assert store.collection.count() == 1  # Vector write already happened.
    retry = await store.http.post('/api/v1/search/index', params={'doc_id': 'doc'})
    assert retry.status_code == 200, retry.text
    assert await marker(store) == 'chunk'
    assert store.collection.count() == 1


async def test_optional_metadata_failure_can_retry(store, monkeypatch):
    class FailingCleanup:
        def __getattr__(self, name):
            return getattr(store.collection, name)
        def update(self, **kwargs):
            raise OSError('simulated optional metadata cleanup failure')
    monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: FailingCleanup())
    failed = await store.http.post('/api/v1/search/index', params={'doc_id': 'doc'})
    assert failed.status_code == 503, failed.text
    assert await marker(store) is None
    assert store.collection.count() == 1
    monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: store.collection)
    retry = await store.http.post('/api/v1/search/index', params={'doc_id': 'doc'})
    assert retry.status_code == 200, retry.text
    assert await marker(store) == 'chunk'
    assert store.collection.count() == 1


async def test_concurrent_http_index_converges(store, monkeypatch):
    barrier = threading.Barrier(2, timeout=15)
    def simultaneous(chunks):
        barrier.wait()
        return rag.index_chunks(chunks)
    monkeypatch.setattr(routes, 'index_chunks', simultaneous)
    responses = await asyncio.gather(*[
        store.http.post('/api/v1/search/index', params={'doc_id': 'doc'}) for _ in range(2)
    ])
    assert [response.status_code for response in responses] == [200, 200]
    assert store.collection.count() == 1
    assert await marker(store) == 'chunk'


async def test_threaded_upsert_keeps_one_id_and_refreshes_cache(store):
    assert rag.index_chunks([{'id': 'chunk', 'text': 'old', 'metadata': {'doc_id': 'doc'}}]) == 1
    assert rag._bm25_index(store.collection).docs == ['old']
    barrier = threading.Barrier(4, timeout=15)
    def write(_):
        barrier.wait()
        return rag.index_chunks([{'id': 'chunk', 'text': 'new biology', 'metadata': {'doc_id': 'doc'}}])
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(write, range(4))) == [1, 1, 1, 1]
    assert store.collection.count() == 1
    assert rag._bm25_index(store.collection).docs == ['new biology']


async def test_successful_empty_tool_and_provider(store):
    tool = await KnowledgeSearchTool().execute(query='biology')
    assert tool.success is True
    assert tool.output == {'results': [], 'context': ''}
    provider = await ChromaDBPipeline().search('biology')
    assert provider.results == [] and provider.context == ''


async def test_provider_query_runs_outside_event_loop_thread(monkeypatch):
    caller = threading.get_ident()
    calls = []
    def query(*args, **kwargs):
        calls.append({'thread': threading.get_ident(), 'strict': kwargs.get('strict')})
        return []
    monkeypatch.setattr(rag, 'search', query)
    assert (await ChromaDBPipeline().search('biology')).results == []
    assert calls[0]['strict'] is True
    assert calls[0]['thread'] != caller


async def test_failed_query_tool_and_provider_keep_internal_paths_private(store, monkeypatch):
    monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: BrokenQuery())
    tool = await KnowledgeSearchTool().execute(query='biology')
    assert tool.success is False
    assert '重试' in tool.error and 'secret.sqlite' not in tool.error
    with pytest.raises(RuntimeError, match='重试') as error:
        await ChromaDBPipeline().search('biology')
    assert 'secret.sqlite' not in str(error.value)


async def test_real_sqlite_write_lock_retries_after_release(store):
    # A real competing transaction, rather than a mocked commit failure.
    path = store.factory.kw['bind'].url.database
    with sqlite3.connect(path, isolation_level=None) as lock:
        lock.execute('BEGIN IMMEDIATE')
        try:
            response = await store.http.post('/api/v1/search/index', params={'doc_id': 'doc'})
            assert response.status_code == 503, response.text
            assert '重试' in response.json()['detail']
            assert 'database is locked' not in response.text
            assert store.collection.count() == 1
        finally:
            lock.rollback()
    assert await marker(store) is None
    retry = await store.http.post('/api/v1/search/index', params={'doc_id': 'doc'})
    assert retry.status_code == 200, retry.text
    assert await marker(store) == 'chunk'
    assert store.collection.count() == 1


@asynccontextmanager
async def localhost(app):
    import uvicorn
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level='critical', lifespan='off'))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{listener.getsockname()[1]}', timeout=15, trust_env=False) as client:
            yield client
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, 5)
        finally:
            listener.close()


async def test_real_localhost_http_outage_and_sql_lock_recovery(store, monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    async def database():
        async with store.factory() as db:
            yield db
    app.dependency_overrides[get_db] = database
    records = []
    async with localhost(app) as client:
        monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: None)
        failed = await client.post('/api/v1/search/', json={'query': '线粒体'})
        assert failed.status_code == 503
        records.append({'stage': 'unavailable', 'status': failed.status_code, 'body': failed.json()})
        monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: store.collection)
        path = store.factory.kw['bind'].url.database
        with sqlite3.connect(path, isolation_level=None) as lock:
            lock.execute('BEGIN IMMEDIATE')
            try:
                failed = await client.post('/api/v1/search/index', params={'doc_id': 'doc'})
                assert failed.status_code == 503
                records.append({'stage': 'sql_locked', 'status': failed.status_code, 'body': failed.json(),
                                'vectors': store.collection.count()})
            finally:
                lock.rollback()
        assert await marker(store) is None
        retry = await client.post('/api/v1/search/index', params={'doc_id': 'doc'})
        assert retry.status_code == 200
        records.append({'stage': 'retry', 'status': retry.status_code, 'body': retry.json(),
                        'vectors': store.collection.count(), 'sql_marker': await marker(store)})
        for endpoint in ['/', '/rag']:
            found = await client.post('/api/v1/search' + endpoint, json={'query': '线粒体'})
            assert found.status_code == 200
            hits = found.json().get('results', found.json().get('sources'))
            assert hits[0]['id'] == 'chunk' and hits[0]['metadata']['doc_id'] == 'doc'
            records.append({'stage': 'recovered' + endpoint, 'status': found.status_code, 'body': found.json()})
        assert store.collection.count() == 1
    evidence = os.environ.get('ROUND06A1_EVIDENCE_DIR')
    if evidence:
        Path(evidence, 'real-http-recovery.json').write_text(json.dumps({
            'transport': 'real localhost TCP/HTTP, FastAPI/Uvicorn',
            'storage': 'real temporary SQLite and ChromaDB',
            'embedding': 'explicit deterministic simulation, no model API',
            'fault': 'real SQLite competing BEGIN IMMEDIATE transaction',
            'records': records,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
