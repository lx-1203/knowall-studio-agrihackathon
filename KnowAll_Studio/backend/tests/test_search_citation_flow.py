"""Real isolated SQL/Chroma/HTTP; embeddings, web and model are simulations."""
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import chromadb
import httpx
import pytest
from chromadb.config import Settings
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import search as routes
from app.core import rag
from app.core.agents.research_agent import ResearchAgent, ResearchTask, SubTopic
from app.database import Base, get_db
from app.models import Document, DocumentChunk


class SimulatedEmbedding(chromadb.EmbeddingFunction):
    def __init__(self):
        pass

    def get_config(self):
        return {}

    @staticmethod
    def build_from_config(config):
        return SimulatedEmbedding()

    def __call__(self, input):
        return [[1.0, float(len(text) % 7) / 10, 0.1] for text in input]

    @staticmethod
    def name():
        return 'round06a_simulated_embedding'


@pytest.fixture
async def store(tmp_path, monkeypatch):
    client = chromadb.PersistentClient(path=str(tmp_path/'vectors'), settings=Settings(anonymized_telemetry=False))
    collection = client.get_or_create_collection('document_knowledge', embedding_function=SimulatedEmbedding())
    monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: collection)
    rag._bm25_cache.clear()
    database_url = 'sqlite+aiosqlite:///' + (tmp_path/'source.sqlite').as_posix()
    monkeypatch.setattr(rag.settings, 'database_url', database_url)
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[Document.__table__, DocumentChunk.__table__])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(Document(id='doc', filename='biology.txt', file_type='txt', sha256='temporary-source'))
        db.add(DocumentChunk(id='chunk', doc_id='doc', chunk_index=0,
                             text_content='线粒体参与能量转换', content_hash='temporary-chunk'))
        await db.commit()
    async def database():
        async with factory() as db:
            yield db
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://search') as http:
        yield SimpleNamespace(http=http, collection=collection, factory=factory)
    await engine.dispose()
    rag._bm25_cache.clear()


async def test_default_metadata_indexes_into_real_chroma(store):
    response = await store.http.post('/api/v1/search/index', params={'doc_id': 'doc'})
    assert response.status_code == 200, response.text
    assert response.json()['indexed'] == 1
    assert store.collection.get(ids=['chunk'])['metadatas'][0]['doc_id'] == 'doc'
    async with store.factory() as db:
        assert (await db.get(DocumentChunk, 'chunk')).vector_id == 'chunk'


async def test_reindex_updates_same_chunk_content_and_metadata(store):
    chunks = [{'id': 'chunk', 'text': 'old biology', 'metadata': {'doc_id': 'doc', 'page_range': '1'}}]
    assert rag.index_chunks(chunks) == 1
    rag._bm25_index(store.collection)
    chunks[0].update(text='updated biology', metadata={'doc_id': 'doc', 'page_range': '2'})
    assert rag.index_chunks(chunks) == 1
    data = store.collection.get(ids=['chunk'])
    assert data['documents'] == ['updated biology']
    assert data['metadatas'][0]['page_range'] == '2'
    assert rag._bm25_index(store.collection).docs == ['updated biology']


async def test_unavailable_index_never_marks_database_success(store, monkeypatch):
    monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: None)
    response = await store.http.post('/api/v1/search/index', params={'doc_id': 'doc'})
    assert response.status_code == 503, response.text
    async with store.factory() as db:
        assert (await db.get(DocumentChunk, 'chunk')).vector_id is None


async def test_reindex_clears_removed_optional_page_metadata(store):
    assert rag.index_chunks([{'id': 'clear-page', 'text': '正文', 'metadata': {'doc_id': 'doc', 'page_range': '2'}}]) == 1
    assert rag.index_chunks([{'id': 'clear-page', 'text': '新正文', 'metadata': {'doc_id': 'doc', 'page_range': None}}]) == 1
    assert 'page_range' not in store.collection.get(ids=['clear-page'])['metadatas'][0]


async def test_missing_document_index_returns_not_found(store):
    response = await store.http.post('/api/v1/search/index', params={'doc_id': 'missing'})
    assert response.status_code == 404


@pytest.mark.parametrize('body', [{'query': ' '}, {'query': 'x', 'top_k': 0}, {'query': 'x', 'top_k': -1}, {'query': 'x', 'top_k': 101}, {'query': 'x'*4001}])
async def test_invalid_search_requests_are_rejected(store, body):
    response = await store.http.post('/api/v1/search/', json=body)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize('hybrid', [False, True])
async def test_context_sources_match_actual_real_index_fragments(store, monkeypatch, hybrid):
    async with store.factory() as db:
        db.add(Document(id='doc2', filename='biology2.txt', file_type='txt', sha256='temporary-source-2'))
        await db.commit()
    monkeypatch.setattr(rag.settings, 'hybrid_retrieval_enabled', hybrid)
    chunks = [
        {'id': 'one', 'text': '线粒体能量', 'metadata': {'doc_id': 'doc', 'page_range': '1-2', 'chunk_index': 0}},
        {'id': 'two', 'text': '细胞核遗传', 'metadata': {'doc_id': 'doc2', 'page_range': '5', 'chunk_index': 2}},
    ]
    assert rag.index_chunks(chunks) == 2
    response = await store.http.post('/api/v1/search/rag', json={'query': '  线粒体  ', 'top_k': 2})
    data = response.json()
    assert data['query'] == '线粒体'
    assert data['has_results'] is True
    assert len(data['sources']) == 2
    for i, source in enumerate(data['sources'], 1):
        assert source['source_id'] == i
        assert source['id'] in ('one', 'two')
        assert source['text'] in data['context']
        assert source['metadata']['doc_id'] in ('doc', 'doc2')
    assert rag.rag_query('线粒体', 2) == data['context']  # Existing callers still receive raw text.


async def test_empty_context_has_no_sources(store):
    data = (await store.http.post('/api/v1/search/rag', json={'query': 'none'})).json()
    assert data == {'query': 'none', 'context': '', 'has_results': False, 'sources': []}


def finding(title, url, snippet='受控发现'):
    return {'title': title, 'url': url, 'snippet': snippet, 'source': 'web'}


def task(findings):
    return ResearchTask(task_id='citation', topic='topic', refined_topic='topic',
                        sub_topics=[SubTopic(id='sub', title='方向', description='', findings=findings)])


async def test_research_assigns_ids_before_prompt_and_deduplicates(monkeypatch):
    from app.core.api_scheduler import api_client
    model = AsyncMock(return_value='受控结论。[来源1] [来源2]')
    monkeypatch.setattr(api_client, 'chat', model)
    report, sources = await ResearchAgent()._generate_report(task([
        finding('A', 'https://example.invalid/a'), finding('A重复', 'https://example.invalid/a'),
        finding('B', 'https://example.invalid/b'),
    ]))
    prompt = model.call_args.args[0]
    assert '[来源1]' in prompt and '[来源2]' in prompt
    assert '[来源3]' not in prompt
    assert '[来源1] A' in prompt and '[来源1] A重复' in prompt
    assert [s['source_id'] for s in sources] == [1, 2]
    assert [s['url'] for s in sources] == ['https://example.invalid/a', 'https://example.invalid/b']
    assert '1. [A]' in report and '2. [B]' in report


@pytest.mark.parametrize('citation', ['[来源0]', '[来源2]', '[来源999]'])
async def test_research_rejects_nonexistent_source_number(monkeypatch, citation):
    from app.core.api_scheduler import api_client
    monkeypatch.setattr(api_client, 'chat', AsyncMock(return_value='错误编号'+citation))
    with pytest.raises(ValueError, match='引用'):
        await ResearchAgent()._generate_report(task([finding('A', 'https://example.invalid/a')]))


async def test_only_sources_in_model_context_appear_in_reference_list(monkeypatch):
    from app.core.api_scheduler import api_client
    model = AsyncMock(return_value='受控结论。[来源1]')
    monkeypatch.setattr(api_client, 'chat', model)
    rows = [finding(f'来源{i}', f'https://example.invalid/{i}', '文本'*100) for i in range(60)]
    _, sources = await ResearchAgent()._generate_report(task(rows))
    context = model.call_args.args[0].split('研究发现：\n')[1].split('\n请按照')[0].strip()
    assert len(context) <= 6000
    assert 0 < len(sources) < len(rows)
    for source in sources:
        assert source['url'] in context
        assert f"[来源{source['source_id']}]" in context


async def test_local_knowledge_keeps_document_chunk_and_page(monkeypatch):
    import app.core.agents.research_agent as module
    from app.core.api_scheduler import api_client
    async def tool(name, **kwargs):
        results = [{'id': 'local-chunk', 'text': '本地能量', 'metadata': {'doc_id': 'doc id', 'page_range': '3-4', 'chunk_index': 2}}] if name == 'knowledge_search' else []
        return SimpleNamespace(success=True, output={'results': results} if name == 'knowledge_search' else results)
    monkeypatch.setattr(module, 'execute_tool', tool)
    monkeypatch.setattr(api_client, 'chat', AsyncMock(return_value='本地结论。[来源1]'))
    agent = ResearchAgent()
    findings = await agent._research_subtopic(SubTopic(id='sub', title='方向', description=''), '主题')
    report, sources = await agent._generate_report(task(findings))
    assert len(sources) == 1
    assert sources[0]['doc_id'] == 'doc id'
    assert sources[0]['chunk_id'] == 'local-chunk'
    assert sources[0]['page_range'] == '3-4'
    assert '/materials/doc%20id' in report


async def test_unlocated_findings_do_not_receive_fabricated_source(monkeypatch):
    from app.core.api_scheduler import api_client
    model = AsyncMock(return_value='仅讨论已有发现。')
    monkeypatch.setattr(api_client, 'chat', model)
    _, sources = await ResearchAgent()._generate_report(task([finding('未定位', '')]))
    assert sources == []
    assert '[来源1]' not in model.call_args.args[0]


async def test_actual_knowledge_tool_keeps_real_index_provenance(store):
    from app.core.tools.knowledge_search import KnowledgeSearchTool
    assert rag.index_chunks([{'id': 'provenance', 'text': '线粒体能量', 'metadata': {'doc_id': 'doc', 'page_range': '2-3'}}]) == 1
    result = await KnowledgeSearchTool().execute(query='线粒体', top_k=1)
    assert result.success, result.error
    hit = result.output['results'][0]
    assert hit['id'] == 'provenance'
    assert hit['metadata']['doc_id'] == 'doc'
    assert hit['metadata']['page_range'] == '2-3'


async def test_graph_tool_existing_result_shape_remains_supported(monkeypatch):
    import app.core.graph_rag
    from app.core.graph_rag import RetrievalResult
    from app.core.tools.knowledge_search import KnowledgeSearchTool
    item = RetrievalResult(source='graph', node_id='node', title='图节点', text='图结果', relevance_score=0.8)
    monkeypatch.setattr(app.core.graph_rag, 'graph_rag_query', AsyncMock(return_value=('图结果', [item])))
    result = await KnowledgeSearchTool().execute(query='图', mode='graphrag')
    assert result.success, result.error
    assert result.output['results'][0]['text'] == '图结果'


async def test_vector_tool_does_not_block_the_async_research_loop(monkeypatch):
    from app.core.tools.knowledge_search import KnowledgeSearchTool
    caller = threading.get_ident()
    calls = []
    def retrieve(*args, **kwargs):
        calls.append(threading.get_ident())
        return []
    monkeypatch.setattr(rag, 'search', retrieve)
    assert (await KnowledgeSearchTool().execute(query='query')).success
    assert calls and calls[0] != caller


@pytest.mark.parametrize('marker', ['[来源-1]', '[来源1,999]', '[来源A]'])
async def test_malformed_citation_does_not_bypass_validation(monkeypatch, marker):
    from app.core.api_scheduler import api_client
    monkeypatch.setattr(api_client, 'chat', AsyncMock(return_value='错误引用'+marker))
    with pytest.raises(ValueError, match='引用'):
        await ResearchAgent()._generate_report(task([finding('A', 'https://example.invalid/a')]))


async def test_local_index_to_research_reference_end_to_end(store, monkeypatch):
    import app.core.agents.research_agent as module
    from app.core.api_scheduler import api_client
    from app.core.tools.knowledge_search import KnowledgeSearchTool
    assert rag.index_chunks([{'id': 'local', 'text': '本地能量转换', 'metadata': {'doc_id': 'doc', 'page_range': '7'}}]) == 1
    async def execute(name, **kwargs):
        if name == 'knowledge_search':
            return await KnowledgeSearchTool().execute(**kwargs)
        return SimpleNamespace(success=True, output=[])  # No external searches.
    monkeypatch.setattr(module, 'execute_tool', execute)
    monkeypatch.setattr(api_client, 'chat', AsyncMock(return_value='本地能量结论。[来源1]'))
    agent = ResearchAgent()
    findings = await agent._research_subtopic(SubTopic(id='s', title='能量', description=''), '能量')
    report, sources = await agent._generate_report(task(findings))
    assert sources[0]['doc_id'] == 'doc' and sources[0]['chunk_id'] == 'local'
    assert sources[0]['page_range'] == '7'
    assert '/materials/doc' in report
