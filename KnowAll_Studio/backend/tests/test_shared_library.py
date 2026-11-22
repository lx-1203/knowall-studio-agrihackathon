"""Real SQL/API checks for opt-in document sharing and private knowledge saves."""
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.core.auth import get_current_user
from app.models import Document, DocumentChunk
from app.models.user import KnowledgePoint, User


@pytest.fixture
async def library(tmp_path):
    from app.api import shared_library, knowledge_points
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'library.sqlite').as_posix())
    async with engine.begin() as connection:
        await connection.run_sync(lambda c: Base.metadata.create_all(c, tables=[
            User.__table__, Document.__table__, DocumentChunk.__table__, KnowledgePoint.__table__,
        ]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        for account in ['alice', 'bob']:
            db.add(User(id=account, username=account, email=account+'@example.com', password_hash='unused'))
        for identity, owner, shared in [('public', 'alice', True), ('private', 'alice', False), ('legacy', None, True)]:
            db.add(Document(id=identity, filename=identity+'.txt', file_type='txt', sha256=identity,
                            status='ready', metadata_={'knowledge_owner_id': owner, 'library_shared': shared}))
        await db.flush()
        for identity in ['public', 'private', 'legacy']:
            db.add(DocumentChunk(id=identity+'-chunk', doc_id=identity, chunk_index=0,
                                 text_content='细胞核 regulates genes '+identity, page_range='1'))
        await db.commit()
        app = FastAPI()
        app.include_router(shared_library.router)
        app.include_router(knowledge_points.router)
        async def database():
            yield db
        account = {'id': 'bob'}
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=account['id'])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            yield SimpleNamespace(client=client, db=db, account=account, app=app)
    await engine.dispose()


async def test_blank_search_never_lists_the_library(library):
    for q in ['', '   ']:
        res = await library.client.get('/api/v1/shared-library/search', params={'q': q})
        assert res.status_code == 200
        assert res.json()['items'] == []


async def test_search_only_matches_explicitly_shared_owned_documents(library):
    res = await library.client.get('/api/v1/shared-library/search', params={'q': '细胞核'})
    assert res.status_code == 200
    assert [hit['document_id'] for hit in res.json()['items']] == ['public']
    assert (await library.client.get('/api/v1/shared-library/search', params={'q': '%'})).json()['items'] == []


async def test_only_owner_can_choose_sharing(library):
    path = '/api/v1/shared-library/documents/private/sharing'
    assert (await library.client.patch(path, json={'shared': True})).status_code == 404
    library.account['id'] = 'alice'
    assert (await library.client.patch(path, json={'shared': True})).json()['shared'] is True
    mine = (await library.client.get('/api/v1/shared-library/mine')).json()['items']
    assert {item['id'] for item in mine} == {'public', 'private'}


async def test_add_saves_private_knowledge_and_is_idempotent(library):
    path = '/api/v1/shared-library/documents/public/add'
    first = await library.client.post(path, json={'chunk_id': 'public-chunk'})
    assert first.status_code == 200
    identity = first.json()['knowledge_point_id']
    assert (await library.client.post(path, json={'chunk_id': 'public-chunk'})).json()['knowledge_point_id'] == identity
    assert await library.db.scalar(select(func.count()).select_from(KnowledgePoint)) == 1
    saved = await library.db.get(KnowledgePoint, identity)
    assert saved.user_id == 'bob'
    assert '细胞核' in saved.content and 'public.txt' in saved.content
    assert (await library.client.get('/api/v1/shared-library/knowledge/search', params={'q': ''})).json()['items'] == []
    matches = (await library.client.get('/api/v1/shared-library/knowledge/search', params={'q': '细胞核'})).json()['items']
    assert [point['id'] for point in matches] == [identity]
    assert (await library.client.get('/api/v1/shared-library/knowledge/search', params={'q': '%'})).json()['items'] == []
    assert (await library.client.get('/api/v1/shared-library/search', params={'q': '细胞核'})).json()['items'][0]['added'] is True
    library.account['id'] = 'alice'
    assert (await library.client.get('/api/v1/knowledge-points/'+identity)).status_code == 404
    assert (await library.client.get('/api/v1/shared-library/knowledge/search', params={'q': '细胞核'})).json()['items'] == []


async def test_withdrawal_prevents_search_and_new_saves(library):
    library.account['id'] = 'alice'
    await library.client.patch('/api/v1/shared-library/documents/public/sharing', json={'shared': False})
    library.account['id'] = 'bob'
    assert (await library.client.get('/api/v1/shared-library/search', params={'q': '细胞核'})).json()['items'] == []
    assert (await library.client.post('/api/v1/shared-library/documents/public/add', json={'chunk_id': 'public-chunk'})).status_code == 404


async def test_private_or_wrong_source_cannot_be_added(library):
    assert (await library.client.post('/api/v1/shared-library/documents/private/add', json={'chunk_id': 'private-chunk'})).status_code == 404
    assert (await library.client.post('/api/v1/shared-library/documents/public/add', json={'chunk_id': 'private-chunk'})).status_code == 404


async def test_withdrawal_preserves_previously_saved_private_copy(library):
    result = await library.client.post('/api/v1/shared-library/documents/public/add', json={'chunk_id': 'public-chunk'})
    identity = result.json()['knowledge_point_id']
    library.account['id'] = 'alice'
    await library.client.patch('/api/v1/shared-library/documents/public/sharing', json={'shared': False})
    library.account['id'] = 'bob'
    assert (await library.client.get('/api/v1/knowledge-points/'+identity)).status_code == 200


async def test_unready_document_cannot_be_shared(library):
    library.account['id'] = 'alice'
    doc = await library.db.get(Document, 'private')
    doc.status = 'parsing'
    await library.db.commit()
    result = await library.client.patch('/api/v1/shared-library/documents/private/sharing', json={'shared': True})
    assert result.status_code == 409


async def test_default_api_requires_authentication(library):
    library.app.dependency_overrides.pop(get_current_user)
    assert (await library.client.get('/api/v1/shared-library/search', params={'q': '细胞核'})).status_code == 401
