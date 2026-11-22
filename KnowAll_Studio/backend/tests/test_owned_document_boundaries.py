"""Private uploads cannot leak through legacy global retrieval."""
from types import SimpleNamespace
import asyncio
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

@pytest.fixture
async def boundary(tmp_path, monkeypatch):
    from app.config import settings
    from app.database import Base, get_db
    from app.models import Document, DocumentChunk
    from app.api import search as api, knowledge_collections
    from app.core.auth import get_current_user, get_optional_user
    from app.core import rag
    url = f"sqlite+aiosqlite:///{(tmp_path / 'boundary.sqlite').as_posix()}"
    monkeypatch.setattr(settings, 'database_url', url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[Document.__table__, DocumentChunk.__table__]))
    async with factory() as db:
        for doc_id, owner in [('alice-doc', 'alice'), ('bob-doc', 'bob'), ('legacy', None)]:
            db.add(Document(id=doc_id, filename=doc_id + '.md', sha256=doc_id * 5, file_type='md', status='ready',
                            metadata_={'knowledge_owner_id': owner} if owner else {}))
        await db.flush()
        for doc_id in ['alice-doc', 'bob-doc', 'legacy']:
            db.add(DocumentChunk(id=doc_id + '-chunk', doc_id=doc_id, chunk_index=0,
                                 text_content=doc_id + ' confidential calculus explanation'))
        await db.commit()
        app = FastAPI()
        app.include_router(api.router)
        app.include_router(knowledge_collections.router)
        from app.api import knowledge, questions
        app.include_router(knowledge.router)
        app.include_router(questions.router)
        async def no_keys(*args):
            pass
        monkeypatch.setattr(knowledge, 'load_user_api_keys', no_keys)
        monkeypatch.setattr(questions, 'load_user_api_keys', no_keys)
        async def database():
            yield db
        app.dependency_overrides[get_db] = database
        account = {'id': 'alice'}
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=account['id'])
        app.dependency_overrides[get_optional_user] = lambda: SimpleNamespace(id=account['id']) if account['id'] else None
        calls = []
        def index(chunks):
            calls.append(chunks)
            return len(chunks)
        monkeypatch.setattr(api, 'index_chunks', index)
        class Collection:
            name = 'document_knowledge'
            def query(self, **kwargs):
                return {'ids': [['private-old', 'legacy-hit', 'private-tag']],
                        'documents': [['private old text', 'public text', 'tagged private text']],
                        'metadatas': [[{'doc_id': 'alice-doc'}, {'doc_id': 'legacy'}, {'knowledge_owner_id': 'bob'}]],
                        'distances': [[0.1, 0.2, 0.3]]}
            def count(self):
                return 3
            def get(self, **kwargs):
                return {key: values[0] for key, values in self.query().items() if key != 'distances'}
            def upsert(self, **kwargs):
                calls.append(kwargs)
        monkeypatch.setattr(rag, 'get_or_create_collection', lambda *args: Collection())
        rag._bm25_cache.clear()
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            yield SimpleNamespace(db=db, client=client, calls=calls, account=account, factory=factory)
    await engine.dispose()

@pytest.mark.parametrize('user_id', [None, 'bob'])
async def test_owned_chunk_loader_rejects_anonymous_and_foreign(boundary, user_id):
    from app.core.documents import load_document_chunks
    with pytest.raises(HTTPException) as exc:
        await load_document_chunks(boundary.db, 'alice-doc', user_id=user_id)
    assert exc.value.status_code == 403

async def test_owner_and_legacy_chunk_loads_are_preserved(boundary):
    from app.core.documents import load_document_chunks
    assert [c.id for c in await load_document_chunks(boundary.db, 'alice-doc', user_id='alice')] == ['alice-doc-chunk']
    assert [c.id for c in await load_document_chunks(boundary.db, 'legacy')] == ['legacy-chunk']

async def test_bulk_loader_rejects_mixed_foreign_sources(boundary):
    from app.core.documents import load_documents_with_chunks
    with pytest.raises(HTTPException) as exc:
        await load_documents_with_chunks(boundary.db, ['legacy', 'bob-doc'], user_id='alice')
    assert exc.value.status_code == 403

async def test_global_index_refuses_owned_document_before_embedding(boundary):
    response = await boundary.client.post('/api/v1/search/index', params={'doc_id': 'alice-doc'})
    assert response.status_code == 403
    assert boundary.calls == []

async def test_global_index_keeps_unowned_legacy_document(boundary):
    response = await boundary.client.post('/api/v1/search/index', params={'doc_id': 'legacy'})
    assert response.status_code == 200
    assert response.json()['indexed'] == 1

@pytest.mark.parametrize('method', ['search', 'bm25_search'])
async def test_global_retrieval_filters_old_untagged_private_vectors(boundary, method):
    from app.core import rag
    result = await asyncio.to_thread(getattr(rag, method), 'text', n_results=10, strict=True)
    assert [hit['id'] for hit in result] == ['legacy-hit']

async def test_private_index_core_fails_closed_without_vector_write(boundary):
    from app.core import rag
    count = await asyncio.to_thread(rag.index_chunks, [
        {'id': 'private', 'text': 'private contents', 'metadata': {'doc_id': 'alice-doc'}}])
    assert count == 0
    assert boundary.calls == []

async def test_owned_collection_preview_keeps_owner_and_denies_foreign(boundary):
    own = await boundary.client.post('/api/v1/knowledge-collections/preview', json={'document_ids': ['alice-doc']})
    assert own.status_code == 200, own.text
    boundary.account['id'] = 'bob'
    response = await boundary.client.post('/api/v1/knowledge-collections/preview', json={'document_ids': ['alice-doc']})
    assert response.status_code == 403


async def test_orphan_chunks_are_not_returned_by_either_loader(boundary):
    from app.models import DocumentChunk
    from app.core.documents import load_document_chunks, load_documents_with_chunks
    boundary.db.add(DocumentChunk(id='orphan-chunk', doc_id='deleted', chunk_index=0, text_content='private deleted text'))
    await boundary.db.commit()
    assert await load_document_chunks(boundary.db, 'deleted') == []
    docs, chunks = await load_documents_with_chunks(boundary.db, ['legacy', 'deleted'])
    assert [doc.id for doc in docs] == ['legacy']
    assert 'deleted' not in chunks


async def test_missing_vector_document_fails_closed(boundary):
    from app.core import rag
    hits = [{'id': 'residue', 'text': 'deleted private text', 'metadata': {'doc_id': 'deleted'}}]
    assert await asyncio.to_thread(rag._global_public_hits, hits) == []


async def test_sync_search_inside_running_loop_keeps_public_only(boundary):
    from app.core import rag
    assert [hit['id'] for hit in rag.search('text', strict=True)] == ['legacy-hit']


@pytest.mark.parametrize('endpoint', ['/', '/rag'])
async def test_global_http_retrieval_keeps_private_text_out_of_response(boundary, endpoint):
    response = await boundary.client.post('/api/v1/search' + endpoint, json={'query': 'text'})
    assert response.status_code == 200, response.text
    assert 'private old text' not in response.text
    assert 'tagged private text' not in response.text
    assert 'public text' in response.text


@pytest.mark.parametrize('method', ['search', 'bm25_search'])
async def test_ownership_database_failure_is_safe_service_failure(boundary, monkeypatch, method):
    from app.core import rag
    async def unavailable(ids):
        raise OSError('secret database unavailable')
    monkeypatch.setattr(rag, '_public_document_ids', unavailable)
    with pytest.raises(rag.RetrievalUnavailableError, match='重试'):
        await asyncio.to_thread(getattr(rag, method), 'text', strict=True)
    assert await asyncio.to_thread(getattr(rag, method), 'text') == []


async def test_bulk_loader_refreshes_owner_after_external_change(boundary):
    from sqlalchemy import update
    from app.models import Document
    from app.core.documents import load_documents_with_chunks
    stale = await boundary.db.get(Document, 'alice-doc')
    assert stale.metadata_['knowledge_owner_id'] == 'alice'
    async with boundary.factory() as other:
        await other.execute(update(Document).where(Document.id == 'alice-doc')
                            .values(metadata_={'knowledge_owner_id': 'bob'}))
        await other.commit()
    with pytest.raises(HTTPException) as exc:
        await load_documents_with_chunks(boundary.db, ['alice-doc'], user_id='alice')
    assert exc.value.status_code == 403


@pytest.mark.parametrize('account', [None, 'bob'])
@pytest.mark.parametrize('endpoint,body', [
    ('/api/v1/knowledge/tree/generate', {'document_id': 'alice-doc'}),
    ('/api/v1/knowledge/outline/generate', {'document_id': 'alice-doc'}),
    ('/api/v1/knowledge/summary/generate', {'document_ids': ['alice-doc']}),
    ('/api/v1/quiz/generate', {'document_id': 'alice-doc'}),
])
async def test_unscoped_legacy_generation_cannot_publish_owned_content(boundary, account, endpoint, body):
    boundary.account['id'] = account
    response = await boundary.client.post(endpoint, json=body)
    assert response.status_code == 403, response.text
    assert '知识网络' in response.json()['detail']


@pytest.mark.parametrize('kind', ['tree', 'summary', 'quiz'])
async def test_owner_can_generate_source_bound_resources(boundary, monkeypatch, kind):
    from unittest.mock import AsyncMock
    from app.models import KnowledgeTree, KnowledgeSummary, KnowledgePointNode
    from app.models.system_config import SystemConfig
    from app.api import knowledge, questions
    async with boundary.factory.kw['bind'].begin() as conn:
        for model in (KnowledgeTree, KnowledgeSummary, KnowledgePointNode, SystemConfig):
            await conn.run_sync(model.__table__.create)
    monkeypatch.setattr(knowledge.knowledge_generator, 'generate_tree', AsyncMock(return_value={'nodes': []}))
    monkeypatch.setattr(knowledge.knowledge_generator, 'generate_summary', AsyncMock(return_value={
        'content_md': '# 本人资料', 'node_count': 0, 'level_stats': {}, 'nodes': []}))
    glossary = AsyncMock(return_value={})
    monkeypatch.setattr('app.core.glossary.extractor.sync_glossary_from_db', glossary)
    monkeypatch.setattr(questions, 'generate_section_questions', AsyncMock(return_value=([
        {'question_text': '这是本人的题目吗？', 'answer': 'A', 'options': [{'label': 'A', 'text': '是'}, {'label': 'B', 'text': '否'}], 'source_chunk_id': 'alice-doc-chunk'}
    ], {}, [])))
    path, body = {
        'tree': ('knowledge/tree', {'document_id': 'alice-doc'}),
        'summary': ('knowledge/summary', {'document_ids': ['alice-doc']}),
        'quiz': ('quiz', {'document_id': 'alice-doc'}),
    }[kind]
    response = await boundary.client.post('/api/v1/' + path + '/generate', json=body)
    assert response.status_code == 200, response.text
    if kind == 'tree':
        resource = '/api/v1/knowledge/tree/' + response.json()['tree_id']
    elif kind == 'summary':
        resource = '/api/v1/knowledge/summary/' + response.json()['summary_id']
        glossary.assert_not_awaited()
    else:
        return
    assert (await boundary.client.get(resource)).status_code == 200
    boundary.account['id'] = 'bob'
    assert (await boundary.client.get(resource)).status_code == 404
