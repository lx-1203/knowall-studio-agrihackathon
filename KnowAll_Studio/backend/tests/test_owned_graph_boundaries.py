"""Real SQL and disk graph cache; embeddings and model calls are disabled."""
import json
import sqlite3
from types import SimpleNamespace

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


@pytest.fixture
async def owned_graph(tmp_path, monkeypatch):
    from app.config import settings
    from app.core import graph_rag as graph
    from app.database import Base
    from app.models import Document, KnowledgeTree, KnowledgeSummary, KnowledgePointNode, KnowledgeEdge
    database_path = tmp_path / 'source.sqlite'
    url = 'sqlite+aiosqlite:///' + database_path.as_posix()
    monkeypatch.setattr(settings, 'database_url', url)
    engine = create_async_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=[Document.__table__, KnowledgeTree.__table__,
                                                                KnowledgeSummary.__table__, KnowledgePointNode.__table__, KnowledgeEdge.__table__])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([Document(id='public', filename='public.md', file_type='md', sha256='p'),
                    Document(id='public2', filename='public2.md', file_type='md', sha256='q'),
                    Document(id='private', filename='private.md', file_type='md', sha256='r',
                             metadata_={'knowledge_owner_id': 'alice'})])
        db.add(KnowledgeTree(id='tree', name='graph', tree_data={}))
        await db.flush()
        db.add_all([KnowledgePointNode(id='p1', document_id='public', level=1, sequence=1,
                                      title='alpha calculus', explanation='public note one'),
                    KnowledgePointNode(id='p2', document_id='public2', level=1, sequence=2,
                                      title='beta calculus', explanation='public note two'),
                    KnowledgePointNode(id='secret', document_id='private', level=1, sequence=3,
                                      title='secret calculus', explanation='private uploaded learning note')])
        db.add_all([KnowledgeEdge(id='public-edge', tree_id='tree', source_node_id='p1', target_node_id='p2', description='public relation'),
                    KnowledgeEdge(id='private-edge', tree_id='tree', source_node_id='secret', target_node_id='p1', description='private relation')])
        await db.commit()
    monkeypatch.setattr(graph, 'async_session', factory)
    monkeypatch.setattr(graph, '_cache_path', lambda: tmp_path / 'graph.pkl')
    monkeypatch.setattr(graph, 'get_or_create_collection', lambda *args: None)
    monkeypatch.setattr(graph, 'vector_search', lambda *args, **kwargs: [])
    retriever = graph.HybridRetriever()
    monkeypatch.setattr(graph, '_hybrid_retriever', retriever)
    yield SimpleNamespace(graph=graph, factory=factory, retriever=retriever, path=database_path)
    await engine.dispose()


async def change_owner(fixture, document_id='public', owner='alice'):
    from app.models import Document
    async with fixture.factory() as db:
        await db.execute(update(Document).where(Document.id == document_id)
                         .values(metadata_={'knowledge_owner_id': owner}))
        await db.commit()


async def test_cold_graph_removes_owned_nodes_and_incident_edges(owned_graph):
    graph = owned_graph.graph.KnowledgeGraph()
    assert await graph.build(force_rebuild=True) == 2
    assert set(graph._nodes) == {'p1', 'p2'}
    assert graph.edge_count == 1
    assert 'private relation' not in repr(graph._adj_out)
    assert not graph.find_nodes_by_query('private uploaded learning note')


async def test_warm_disk_cache_is_filtered_against_current_ownership(owned_graph):
    module = owned_graph.graph
    nodes = {
        'p1': module.GraphNode('p1', 'alpha', 'public explanation', 1, 'public'),
        'secret': module.GraphNode('secret', 'secret', 'cached private explanation', 1, 'private'),
    }
    module._save_graph_to_disk({'secret': [('p1', 'related_to', 'private relation')]},
                              {'p1': [('secret', 'related_to', 'private relation')]}, nodes,
                              {'se': ['secret'], 'al': ['p1']}, 1)
    graph = module.KnowledgeGraph()
    assert await graph.build() == 1
    assert set(graph._nodes) == {'p1'}
    assert graph.edge_count == 0
    assert graph._adj_out == {} and graph._adj_in == {}
    assert 'secret' not in repr(graph._bigram_index)


async def test_graph_query_has_no_private_node_or_relation_in_context(owned_graph):
    context, results = await owned_graph.graph.graph_rag_query('calculus', strict=True)
    assert results
    assert 'private uploaded learning note' not in context
    assert 'private relation' not in context
    assert all(result.metadata['doc_id'] != 'private' for result in results)


async def test_in_memory_graph_rechecks_uncached_ownership_changes(owned_graph):
    first, _ = await owned_graph.graph.graph_rag_query('alpha', strict=True)
    assert 'public note one' in first
    await change_owner(owned_graph)
    context, results = await owned_graph.graph.graph_rag_query('alpha', strict=True)
    assert 'public note one' not in context
    assert all(result.metadata['doc_id'] != 'public' for result in results)
    assert 'p1' not in owned_graph.retriever._graph._nodes


async def test_final_result_guard_rechecks_changes_during_semantic_search(owned_graph, monkeypatch):
    module = owned_graph.graph
    def search(*args, **kwargs):
        with sqlite3.connect(owned_graph.path) as connection:
            connection.execute('UPDATE documents SET metadata = ? WHERE id = ?',
                               (json.dumps({'knowledge_owner_id': 'alice'}), 'public'))
        return []
    monkeypatch.setattr(owned_graph.retriever._node_index, 'search', search)
    context, results = await module.graph_rag_query('alpha', strict=True)
    assert 'public note one' not in context
    assert 'alpha calculus' not in context  # Also exclude public results whose path traversed the private node.
    assert all(result.metadata['doc_id'] != 'public' for result in results)


async def test_ownership_database_failure_raises_safe_strict_error(owned_graph, monkeypatch):
    module = owned_graph.graph
    async def unavailable(ids):
        raise OSError('private-database-secret')
    monkeypatch.setattr(module, '_public_document_ids', unavailable, raising=False)
    with pytest.raises(module.RetrievalUnavailableError, match='重试') as exc:
        await module.graph_rag_query('calculus', strict=True)
    assert 'private-database-secret' not in str(exc.value)


async def test_empty_graph_vector_fallback_rechecks_owner_before_return(owned_graph, monkeypatch):
    module = owned_graph.graph
    monkeypatch.setattr(module, 'vector_search', lambda *args, **kwargs: [
        {'id': 'vector', 'text': 'source text before ownership changed', 'distance': 0.1, 'metadata': {'doc_id': 'public'}}])
    async def empty_graph():
        await change_owner(owned_graph)
        return module.KnowledgeGraph()
    monkeypatch.setattr(owned_graph.retriever, '_ensure_graph', empty_graph)
    context, results = await module.graph_rag_query('alpha', strict=True)
    assert context == ''
    assert results == []


async def test_dangling_edges_cannot_influence_public_graph_counts(owned_graph):
    from sqlalchemy import delete
    from app.models import KnowledgePointNode
    async with owned_graph.factory() as db:
        await db.execute(delete(KnowledgePointNode).where(KnowledgePointNode.id == 'secret'))
        await db.commit()
    graph = owned_graph.graph.KnowledgeGraph()
    assert await graph.build(force_rebuild=True) == 2
    assert graph.edge_count == 1
    assert 'private relation' not in repr(graph._adj_out)
