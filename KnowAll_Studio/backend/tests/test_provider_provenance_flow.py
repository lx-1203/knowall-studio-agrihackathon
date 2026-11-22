"""Real isolated graph/SQLite/Chroma; Llama components and AI explicitly simulated."""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import chromadb
from chromadb.config import Settings as ChromaSettings
import httpx
import pytest
from fastapi import FastAPI

from app.api import chat as routes
from app.core import graph_rag as graph, rag
from app.core.agents.research_agent import ResearchAgent, ResearchTask, SubTopic
from app.core.rag_assistant import RAGAssistant
from app.core.rag_pipeline import factory
from app.core.rag_pipeline.providers.graphrag_pipeline import GraphRAGPipeline
from app.core.rag_pipeline.providers import llamaindex_pipeline as llama
from app.core.tools.knowledge_search import KnowledgeSearchTool
from app.database import Base
from app.models import Document, KnowledgeTree, KnowledgeSummary, KnowledgePointNode, KnowledgeEdge
from sqlalchemy import delete
from tests.test_search_citation_flow import SimulatedEmbedding, store  # noqa: F401
from tests.test_search_recovery_flow import localhost


@pytest.fixture
async def graph_store(store, tmp_path, monkeypatch):
    engine = store.factory.kw['bind']
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[
            KnowledgeTree.__table__, KnowledgeSummary.__table__, KnowledgePointNode.__table__, KnowledgeEdge.__table__,
        ])
    async with store.factory() as db:
        db.add(Document(id='doc2', filename='chemistry.txt', file_type='txt', sha256='temporary-chemistry'))
        db.add(KnowledgeTree(id='tree', name='temporary graph', tree_data={}))
        db.add(KnowledgePointNode(id='node1', document_id='doc', level=1, sequence=1,
                                 title='线粒体', explanation='细胞内部特定细胞器，拥有双层膜结构。'))
        db.add(KnowledgePointNode(id='node2', document_id='doc2', level=2, sequence=1,
                                 title='氧化磷酸化', explanation='电子传递链利用跨膜质子梯度耦联化学反应。'))
        db.add(KnowledgePointNode(id='node3', document_id='doc2', level=2, sequence=2,
                                 title='植物光合作用', explanation='叶绿素吸收光子使二氧化碳还原为糖类物质。'))
        db.add(KnowledgeEdge(id='edge', tree_id='tree', source_node_id='node1', target_node_id='node2',
                             relation_type='related_to', description='能量过程'))
        await db.commit()
    client = chromadb.PersistentClient(path=str(tmp_path/'node-vectors'), settings=ChromaSettings(anonymized_telemetry=False))
    nodes = client.get_or_create_collection('knowledge_nodes', embedding_function=SimulatedEmbedding())
    def collection(name='document_knowledge'):
        return nodes if name == 'knowledge_nodes' else store.collection
    monkeypatch.setattr(graph, 'async_session', store.factory)
    monkeypatch.setattr(graph, '_cache_path', lambda: tmp_path/'graph.pkl')
    monkeypatch.setattr(graph, 'get_or_create_collection', collection)
    monkeypatch.setattr(rag, 'get_or_create_collection', collection)
    retriever = graph.HybridRetriever()
    monkeypatch.setattr(graph, '_hybrid_retriever', retriever)
    assert rag.index_chunks([{'id': 'chunk', 'text': 'ATP synthesis citation fragment.',
                             'metadata': {'doc_id': 'doc', 'page_range': '7-8', 'chunk_index': 0}}]) == 1
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://graph') as http:
        yield SimpleNamespace(store=store, retriever=retriever, nodes=nodes, app=app, http=http)


def assert_sources(results):
    vector = next(row for row in results if row.source == 'vector')
    assert vector.id == 'chunk'
    assert vector.metadata['doc_id'] == 'doc'
    assert vector.metadata['page_range'] == '7-8'
    node = next(row for row in results if row.node_id == 'node2')
    assert node.id == 'node2'
    assert node.metadata['doc_id'] == 'doc2'
    assert node.metadata['node_id'] == 'node2'
    assert 'page_range' not in node.metadata  # Graph has no original page evidence.


async def test_real_graph_retrieval_keeps_chunk_and_node_provenance(graph_store):
    results = await graph_store.retriever.retrieve('线粒体')
    assert_sources(results)
    limited = await graph_store.retriever.retrieve('线粒体', max_graph_results=1)
    semantic = [row for row in limited if row.source == 'node_semantic']
    assert semantic
    for row in semantic:
        assert row.metadata['doc_id'] == {'node1': 'doc', 'node2': 'doc2', 'node3': 'doc2'}[row.node_id]
        assert row.id == row.node_id


async def test_real_relationship_provenance_with_optional_semantic_unavailable(graph_store, monkeypatch):
    monkeypatch.setattr(graph, 'get_or_create_collection', lambda name: None)
    results = await graph_store.retriever.retrieve('线粒体', strict=True)
    relation = next(row for row in results if '--[related_to]-->' in row.relation_path)
    assert relation.id == 'node2'
    assert relation.metadata['doc_id'] == 'doc2'
    assert '能量过程' in relation.relation_path
    assert_sources(results)


async def test_successful_empty_graph_is_200_and_not_failure(graph_store):
    graph_store.store.collection.delete(ids=['chunk'])
    async with graph_store.store.factory() as db:
        await db.execute(delete(KnowledgeEdge))
        await db.execute(delete(KnowledgePointNode))
        await db.commit()
    response = await graph_store.http.post('/api/v1/chat/search/graphrag', params={'query': 'empty'})
    assert response.status_code == 200
    assert response.json()['results'] == [] and response.json()['context'] == ''
    assert (await KnowledgeSearchTool().execute(query='empty', mode='graphrag')).success is True
    assert (await GraphRAGPipeline().search('empty')).results == []


def test_beam_pruning_more_seeds_than_width_terminates_in_isolated_process():
    script = '''from app.core.graph_rag import KnowledgeGraph, GraphNode
g = KnowledgeGraph()
g._nodes = {str(i): GraphNode(str(i), str(i), 'text', 1, 'doc') for i in range(6)}
print('READY_FOR_BEAM', flush=True)
results = g.beam_traverse(list(g._nodes), beam_width=5, max_hops=0)
assert [node.node_id for node, _, _ in results] == ['0', '1', '2', '3', '4']
print('BEAM_RETURNED', flush=True)
'''
    try:
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=8)
    except subprocess.TimeoutExpired as error:
        pytest.fail(f'beam traversal did not terminate; child killed after 8s; stdout={error.stdout!r}')
    assert result.returncode == 0, result.stderr
    assert 'BEAM_RETURNED' in result.stdout


async def test_warm_graph_restore_keeps_original_document_ids(graph_store):
    await graph_store.retriever.retrieve('线粒体')
    restored = graph.KnowledgeGraph()
    assert await restored.build() == 3
    assert restored._nodes['node2'].document_id == 'doc2'
    graph_store.retriever._graph = restored
    assert_sources(await graph_store.retriever.retrieve('线粒体'))


async def test_graph_provider_and_tool_keep_provenance(graph_store):
    provider = await GraphRAGPipeline().search('线粒体')
    by_id = {row.id: row for row in provider.results}
    assert by_id['chunk'].metadata['page_range'] == '7-8'
    assert by_id['node2'].metadata['doc_id'] == 'doc2'
    tool = await KnowledgeSearchTool().execute(query='线粒体', mode='graphrag')
    assert tool.success, tool.error
    hits = {row['id']: row for row in tool.output['results']}
    assert hits['chunk']['metadata']['doc_id'] == 'doc'
    assert hits['node2']['metadata']['doc_id'] == 'doc2'


async def test_graph_search_api_transmits_locators(graph_store):
    response = await graph_store.http.post('/api/v1/chat/search/graphrag', params={'query': '线粒体'})
    assert response.status_code == 200, response.text
    data = response.json()
    by_id = {row['id']: row for row in data['results']}
    assert by_id['chunk']['metadata']['page_range'] == '7-8'
    assert by_id['node2']['metadata']['doc_id'] == 'doc2'
    assert by_id['node2']['node_id'] == 'node2'
    assert data['result_count'] == len(data['results'])


@pytest.mark.parametrize('params', [{'query': ' '}, {'query': 'x', 'top_k': 0},
                                    {'query': 'x', 'top_k': 101}, {'query': 'x'*4001}])
async def test_graph_search_api_validates_before_retrieval(graph_store, monkeypatch, params):
    spy = AsyncMock(return_value={})
    monkeypatch.setattr(routes.rag_assistant, 'search_graphrag', spy)
    response = await graph_store.http.post('/api/v1/chat/search/graphrag', params=params)
    assert response.status_code == 422, response.text
    assert spy.await_count == 0


async def test_legacy_graph_result_has_no_fabricated_source(monkeypatch):
    item = graph.RetrievalResult('graph', 'legacy', '旧节点', '旧正文', 0.8)
    monkeypatch.setattr(graph, 'graph_rag_query', AsyncMock(return_value=('旧正文', [item])))
    monkeypatch.setattr(graph, 'get_graph_stats', AsyncMock(return_value={'node_count': 0}))
    provider = await GraphRAGPipeline().search('legacy')
    assert provider.results[0].id == 'legacy'
    assert provider.results[0].metadata == {}
    tool = await KnowledgeSearchTool().execute(query='legacy', mode='graphrag')
    assert tool.success
    assert tool.output['results'][0]['metadata'] == {}


async def test_strict_graph_vector_failure_is_safe_and_legacy_can_degrade(graph_store, monkeypatch):
    monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: None)
    response = await graph_store.http.post('/api/v1/chat/search/graphrag', params={'query': '线粒体'})
    assert response.status_code == 503, response.text
    assert '重试' in response.json()['detail']
    tool = await KnowledgeSearchTool().execute(query='线粒体', mode='graphrag')
    assert tool.success is False
    with pytest.raises(RuntimeError, match='重试'):
        await GraphRAGPipeline().search('线粒体')
    context, rows = await graph.graph_rag_query('线粒体', strict=False)
    assert rows and context  # Optional graph path remains available in explicit legacy mode.


async def test_strict_graph_sql_failure_does_not_expose_path(graph_store, monkeypatch):
    def broken_session():
        raise OSError('private/source.sqlite missing')
    monkeypatch.setattr(graph, 'async_session', broken_session)
    response = await graph_store.http.post('/api/v1/chat/search/graphrag', params={'query': '线粒体'})
    assert response.status_code == 503
    assert 'source.sqlite' not in response.text


async def test_graph_blocking_queries_run_off_event_loop(graph_store, monkeypatch):
    caller = threading.get_ident()
    calls = []
    original = graph.vector_search
    def vector(*args, **kwargs):
        calls.append(('vector', threading.get_ident()))
        return original(*args, **kwargs)
    semantic = graph_store.retriever._node_index.search
    def nodes(*args, **kwargs):
        calls.append(('nodes', threading.get_ident()))
        return semantic(*args, **kwargs)
    monkeypatch.setattr(graph, 'vector_search', vector)
    monkeypatch.setattr(graph_store.retriever._node_index, 'search', nodes)
    await graph_store.retriever.retrieve('线粒体')
    assert {kind for kind, _ in calls} == {'vector', 'nodes'}
    assert all(thread != caller for _, thread in calls)


@pytest.fixture
def simulated_llama(monkeypatch, tmp_path):
    """Protocol simulations, explicitly not the installed LlamaIndex/FAISS library."""
    state = SimpleNamespace(vector=[], keywords=[], vector_error=None, bm25_error=None, threads=[])
    class VectorRetriever:
        def __init__(self, **kwargs):
            pass
        def retrieve(self, bundle):
            state.threads.append(threading.get_ident())
            if state.vector_error:
                raise state.vector_error
            return state.vector
    class KeywordRetriever:
        @classmethod
        def from_defaults(cls, **kwargs):
            if state.bm25_error:
                raise state.bm25_error
            return cls()
        def retrieve(self, bundle):
            return state.keywords
    core = ModuleType('llama_index.core')
    core.QueryBundle = lambda query_str: SimpleNamespace(query_str=query_str)
    retrievers = ModuleType('llama_index.core.retrievers')
    retrievers.VectorIndexRetriever = VectorRetriever
    bm25 = ModuleType('llama_index.retrievers.bm25')
    bm25.BM25Retriever = KeywordRetriever
    for name, module in [('llama_index.core', core), ('llama_index.core.retrievers', retrievers),
                         ('llama_index.retrievers.bm25', bm25)]:
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(llama, '_check_deps', lambda: True)
    provider = llama.LlamaIndexPipeline()
    provider._indices['default'] = SimpleNamespace(docstore=SimpleNamespace(docs={}))
    monkeypatch.setattr(provider, '_get_persist_dir', lambda kb: tmp_path/kb)
    monkeypatch.setattr(factory, '_instances', {'llamaindex': provider})
    state.provider = provider
    return state


def scored(node_id, text, metadata):
    node = SimpleNamespace(node_id=node_id, metadata=metadata, get_content=lambda: text)
    return SimpleNamespace(node=node, score=0.8)


@pytest.mark.parametrize('failure', ['dependencies', 'index', 'vector'])
async def test_llama_strict_failures_not_successful_empty(simulated_llama, failure):
    state = simulated_llama
    if failure == 'dependencies':
        state.provider._available = False
    elif failure == 'index':
        state.provider._indices.clear()
    else:
        state.vector_error = OSError('private/index.sqlite failed')
    with pytest.raises(RuntimeError, match='重试') as error:
        await state.provider.search('biology')
    assert 'index.sqlite' not in str(error.value)
    result = await KnowledgeSearchTool().execute(query='biology', mode='llamaindex')
    assert result.success is False
    legacy = await state.provider.search('biology', strict=False)
    assert legacy.results == [] and legacy.context == ''


async def test_simulated_llama_fusion_metadata_and_nonblocking(simulated_llama):
    state = simulated_llama
    a = scored('a', '甲文档段落', {'doc_id': 'doc', 'page_range': '2'})
    b = scored('b', '未定位文件内容', {'filename': 'unmapped.txt', 'file_path': '/temporary/unmapped.txt'})
    state.vector = [a, b]
    state.keywords = [a]
    caller = threading.get_ident()
    result = await state.provider.search('biology', top_k=2)
    assert [row.id for row in result.results] == ['a', 'b']
    assert result.results[0].metadata['page_range'] == '2'
    assert 'doc_id' not in result.results[1].metadata
    assert state.threads and all(thread != caller for thread in state.threads)
    tool = await KnowledgeSearchTool().execute(query='biology', mode='llamaindex')
    assert tool.success
    assert tool.output['results'][0]['metadata']['doc_id'] == 'doc'


async def test_simulated_llama_empty_is_success_and_optional_bm25_can_degrade(simulated_llama):
    state = simulated_llama
    result = await state.provider.search('biology')
    assert result.results == [] and 'error' not in result.stats
    state.vector = [scored('a', '有效向量片段', {'doc_id': 'doc'})]
    state.bm25_error = ImportError('explicit unavailable optional BM25 component')
    result = await state.provider.search('biology')
    assert result.results[0].id == 'a'


@pytest.mark.parametrize('mode', ['graphrag', 'llamaindex'])
async def test_provider_tool_to_research_report_uses_real_locators(graph_store, simulated_llama, monkeypatch, mode):
    import app.core.agents.research_agent as research
    from app.core.api_scheduler import api_client
    simulated_llama.vector = [scored('a', '模拟 Llama 已定位片段', {'doc_id': 'doc', 'page_range': '2'})]
    async def execute(name, **kwargs):
        if name == 'knowledge_search':
            return await KnowledgeSearchTool().execute(**kwargs, mode=mode)
        return SimpleNamespace(success=True, output=[])  # External search explicitly simulated empty.
    monkeypatch.setattr(research, 'execute_tool', execute)
    monkeypatch.setattr(api_client, 'chat', AsyncMock(return_value='有来源的发现。[来源1]'))
    agent = ResearchAgent()
    findings = await agent._research_subtopic(SubTopic(id='s', title='线粒体', description=''), '线粒体')
    task = ResearchTask(task_id='temp', topic='线粒体', refined_topic='线粒体',
                        sub_topics=[SubTopic(id='s', title='线粒体', description='', findings=findings)])
    report, sources = await agent._generate_report(task)
    assert sources and all(source.get('doc_id') in ('doc', 'doc2') for source in sources)
    assert '[来源1]' in report
    assert any(source['chunk_id'] for source in sources)


async def test_llama_filename_without_document_id_does_not_fabricate_research_reference(simulated_llama, monkeypatch):
    import app.core.agents.research_agent as research
    from app.core.api_scheduler import api_client
    simulated_llama.vector = [scored('unmapped', '未定位文件内容', {'filename': 'local.txt', 'file_path': '/temporary/local.txt'})]
    async def execute(name, **kwargs):
        if name == 'knowledge_search':
            return await KnowledgeSearchTool().execute(**kwargs, mode='llamaindex')
        return SimpleNamespace(success=True, output=[])
    monkeypatch.setattr(research, 'execute_tool', execute)
    model = AsyncMock(return_value='未定位发现，不标引用编号。')
    monkeypatch.setattr(api_client, 'chat', model)
    agent = ResearchAgent()
    findings = await agent._research_subtopic(SubTopic(id='s', title='local', description=''), 'local')
    task = ResearchTask(task_id='temp', topic='local', refined_topic='local',
                        sub_topics=[SubTopic(id='s', title='local', description='', findings=findings)])
    _, sources = await agent._generate_report(task)
    assert sources == []
    assert '[来源1]' not in model.call_args.args[0]


async def test_real_localhost_graph_search_source_outage_recovery(graph_store, monkeypatch):
    records = []
    async with localhost(graph_store.app) as client:
        for phase in ['ready', 'outage', 'recovered']:
            if phase == 'outage':
                monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: None)
            elif phase == 'recovered':
                monkeypatch.setattr(rag, 'get_or_create_collection', lambda *a, **kw: graph_store.store.collection)
            response = await client.post('/api/v1/chat/search/graphrag', params={'query': '线粒体'})
            records.append({'phase': phase, 'status': response.status_code, 'body': response.json()})
            assert response.status_code == (503 if phase == 'outage' else 200)
            if phase != 'outage':
                hits = {row['id']: row for row in response.json()['results']}
                assert hits['chunk']['metadata']['doc_id'] == 'doc'
        assert graph_store.store.collection.count() == 1
    evidence = os.environ.get('ROUND06A2_EVIDENCE_DIR')
    if evidence:
        Path(evidence, 'real-http-providers.json').write_text(json.dumps({
            'transport': 'real localhost TCP/HTTP FastAPI/Uvicorn',
            'storage': 'real temporary SQLite graph and ChromaDB',
            'embedding': 'explicit deterministic simulation, no model API',
            'records': records,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
