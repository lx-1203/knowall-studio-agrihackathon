"""Unified search exercises real SQL and permissions with model/network doubles."""
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import search as routes
from app.core.auth import get_optional_user
from app.database import Base, get_db
from app.models import Document, DocumentChunk, Deck, Flashcard, QuestionBank, KnowledgeTree, KnowledgePointNode, KnowledgeSummary, StudyPack
from app.models.user import User, KnowledgePoint
from app.models.glossary import GlossaryTerm
from app.models.knowledge_collection import KnowledgeCollection


@pytest.fixture
async def unified(tmp_path, monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'unified.sqlite').as_posix())
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: Base.metadata.create_all(conn, tables=[model.__table__ for model in [
            User, Document, DocumentChunk, Deck, Flashcard, QuestionBank, KnowledgeTree,
            KnowledgePointNode, KnowledgeSummary, KnowledgePoint, GlossaryTerm, KnowledgeCollection, StudyPack,
        ]]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(User(id='alice', username='alice', email='alice@example.com', password_hash='unused'))
        for identity, owner, shared in [('own', 'alice', False), ('private', 'bob', False), ('shared', 'bob', True)]:
            db.add(Document(id=identity, filename=identity+'.txt', file_type='txt', sha256=identity,
                            status='ready', metadata_={'knowledge_owner_id': owner, 'library_shared': shared}))
        await db.flush()
        for identity in ['own', 'private', 'shared']:
            db.add(DocumentChunk(id=identity+'-chunk', doc_id=identity, chunk_index=0, text_content='derivative '+identity))
        db.add_all([
            KnowledgePoint(id='point', user_id='alice', title='derivative personal', content='calculus'),
            KnowledgePoint(id='foreign-point', user_id='bob', title='derivative private', content='secret'),
            Deck(id='deck', user_id='alice', name='derivative'),
            Deck(id='foreign-deck', user_id='bob', name='derivative'),
            GlossaryTerm(id='term', term='derivative', definition='rate of change', subject='math'),
            GlossaryTerm(id='foreign-term', term='derivative secret', definition='secret', subject='private', source_document_id='private'),
            KnowledgeCollection(id='collection', user_id='alice', title='derivative collection', subject='math', points=[]),
            KnowledgeCollection(id='foreign-collection', user_id='bob', title='derivative secret', subject='math', points=[]),
            KnowledgeTree(id='tree', name='derivative tree', doc_ids=['own'], tree_data={}),
            KnowledgeTree(id='foreign-tree', name='derivative secret', doc_ids=['private'], tree_data={}),
            KnowledgeSummary(id='summary', content_md='derivative summary', document_id='own', document_ids=['own']),
        ])
        await db.flush()
        db.add_all([
            Flashcard(id='card', card_type='qa', deck_id='deck', front='derivative card', back='calculus'),
            Flashcard(id='foreign-card', card_type='qa', deck_id='foreign-deck', front='derivative secret', back='secret'),
            QuestionBank(id='question', question_type='short_answer', question_text='derivative exercise', correct_answer='calculus', source_chunk_id='own-chunk'),
            QuestionBank(id='foreign-question', question_type='short_answer', question_text='derivative secret', correct_answer='secret', source_chunk_id='private-chunk'),
            KnowledgePointNode(id='node', summary_id='summary', document_id='own', level=1, sequence=0, title='derivative node', explanation='calculus'),
        ])
        await db.commit()
    app = FastAPI()
    app.include_router(routes.router)
    async def database():
        async with factory() as session:
            yield session
    account = {'user': SimpleNamespace(id='alice')}
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_optional_user] = lambda: account['user']
    # Patch public integrations before they can make any external calls.
    from app.core import unified_search as service
    async def expand(query, user_id, db):
        return ['derivative']
    async def web(query, limit):
        return [{'title': 'Calculus reference', 'url': 'https://example.com/calculus', 'snippet': 'derivative reference'}]
    async def vectors(query, limit):
        return []
    monkeypatch.setattr(service, 'expand_query', expand)
    monkeypatch.setattr(service, 'web_search', web)
    monkeypatch.setattr(service, 'vector_search', vectors)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield SimpleNamespace(client=client, account=account, service=service)
    await engine.dispose()


async def test_ai_covers_unindexed_and_structured_sources(unified):
    response = await unified.client.post('/api/v1/search/', json={'query': 'how quickly things change', 'mode': 'ai', 'top_k': 50})
    assert response.status_code == 200, response.text
    results = response.json()['results']
    assert {'document', 'knowledge_point', 'collection', 'knowledge_tree', 'summary', 'knowledge_node', 'question', 'flashcard', 'glossary', 'web'} <= {hit['kind'] for hit in results}
    text = str(results)
    assert 'own.txt' in text and 'shared.txt' in text
    assert 'foreign-' not in text and 'private.txt' not in text
    assert response.json()['ai_used'] is True


async def test_network_failure_keeps_local_results_and_warning(unified, monkeypatch):
    async def failure(*args):
        raise RuntimeError('network down')
    monkeypatch.setattr(unified.service, 'web_search', failure)
    response = await unified.client.post('/api/v1/search/', json={'query': 'derivative', 'mode': 'ai'})
    assert response.status_code == 200
    assert response.json()['results']
    assert any('联网' in message for message in response.json()['warnings'])


async def test_model_failure_is_not_reported_as_ai_success(unified, monkeypatch):
    async def failure(*args):
        raise RuntimeError('model down')
    monkeypatch.setattr(unified.service, 'expand_query', failure)
    response = await unified.client.post('/api/v1/search/', json={'query': 'derivative', 'mode': 'ai', 'top_k': 50})
    assert response.status_code == 200
    assert response.json()['ai_used'] is False
    assert response.json()['warnings']
    assert any(hit['kind'] == 'document' for hit in response.json()['results'])


async def test_unsafe_external_links_are_dropped(unified, monkeypatch):
    async def malicious(*args):
        return [{'title': 'bad', 'url': 'javascript:alert(1)', 'snippet': 'derivative'}]
    monkeypatch.setattr(unified.service, 'web_search', malicious)
    response = await unified.client.post('/api/v1/search/', json={'query': 'derivative', 'mode': 'ai'})
    assert not any(hit['kind'] == 'web' for hit in response.json()['results'])


async def test_empty_ai_query_does_not_search(unified):
    response = await unified.client.post('/api/v1/search/', json={'query': '   ', 'mode': 'ai'})
    assert response.status_code == 422


async def test_anonymous_cannot_read_owned_content(unified):
    unified.account['user'] = None
    response = await unified.client.post('/api/v1/search/', json={'query': 'derivative', 'mode': 'ai', 'top_k': 50})
    assert response.status_code == 200
    text = str(response.json()['results'])
    assert 'own.txt' not in text and 'foreign-' not in text
    assert 'shared.txt' in text
    assert not any(hit['kind'] == 'knowledge_point' for hit in response.json()['results'])


async def test_old_vector_metadata_cannot_leak_private_documents(unified, monkeypatch):
    async def vectors(*args):
        return [{'id': 'private-chunk', 'text': 'private vector leak', 'metadata': {'doc_id': 'private'}}]
    monkeypatch.setattr(unified.service, 'vector_search', vectors)
    response = await unified.client.post('/api/v1/search/', json={'query': 'derivative', 'mode': 'ai', 'top_k': 50})
    assert 'private vector leak' not in str(response.json()['results'])


async def test_model_and_internet_receive_only_user_query(unified, monkeypatch):
    calls = []
    async def web(query, limit):
        calls.append(query)
        return []
    monkeypatch.setattr(unified.service, 'web_search', web)
    response = await unified.client.post('/api/v1/search/', json={'query': 'rate of change', 'mode': 'ai'})
    assert response.status_code == 200
    assert calls == ['rate of change']


async def test_builtin_knowledge_collections_are_included(unified, monkeypatch):
    from app.core import knowledge_collections
    monkeypatch.setattr(knowledge_collections, 'builtin_collections', lambda: [
        {'id': 'builtin-test', 'title': 'derivative starter', 'subject': 'math', 'points': [{'definition': 'derivative details'}]},
    ])
    response = await unified.client.post('/api/v1/search/', json={'query': 'derivative', 'mode': 'ai', 'top_k': 50})
    assert any(hit['resource_id'] == 'builtin-test' for hit in response.json()['results'])
