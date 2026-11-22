"""Search real temporary SQLite, without a vector service or live model."""
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import search as routes
from app.core import rag
from app.core.auth import create_access_token, get_optional_user
from app.database import Base, get_db
from app.models import Document, DocumentChunk
from app.models.user import User


@pytest.fixture
async def catalog(tmp_path, monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path/'catalog.sqlite').as_posix())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=[User.__table__, Document.__table__, DocumentChunk.__table__])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        for user_id in ['me', 'other']:
            db.add(User(id=user_id, username=user_id, email=f'{user_id}@example.test', password_hash='unused-in-token-tests'))
        for doc_id, filename, owner, texts in [
            ('public', '计算机网络.pdf', None, ['TCP 三次握手建立连接', 'TCP 传输协议', 'TCP 完整内容', 'TCP 其他内容']),
            ('mine', '生物笔记.txt', 'me', ['线粒体负责能量转换']),
            ('other', '他人线粒体笔记.txt', 'other', ['线粒体的秘密']),
            ('title', '期末复习计划.docx', None, []),
            ('literal', '特殊符号.txt', None, ['100% 完成 _name 标识', '1000 完成 xname 标识']),
        ]:
            db.add(Document(id=doc_id, filename=filename, file_type='txt', sha256=doc_id,
                            metadata_={'knowledge_owner_id': owner} if owner else {}))
            for index, text in enumerate(texts):
                db.add(DocumentChunk(id=f'{doc_id}-{index}', doc_id=doc_id, chunk_index=index,
                                     text_content=text, page_range=str(index+1)))
        await db.commit()
    async def database():
        async with factory() as db:
            yield db
    identity = {'user': SimpleNamespace(id='me')}
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_optional_user] = lambda: identity['user']
    identity['app'] = app
    def no_vectors(*args, **kwargs):
        raise AssertionError('Keyword search must not open the vector service')
    monkeypatch.setattr(rag, 'get_or_create_collection', no_vectors)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://search') as http:
        yield http, identity
    await engine.dispose()


async def find(catalog, query, **kwargs):
    response = await catalog[0].post('/api/v1/search/', json={'query': query, 'mode': 'keyword', **kwargs})
    assert response.status_code == 200, response.text
    return response.json()['results']


async def test_personal_chunks_search_without_vector_index(catalog):
    results = await find(catalog, '线粒体')
    assert [hit['metadata']['doc_id'] for hit in results] == ['mine']
    assert results[0]['metadata']['filename'] == '生物笔记.txt'
    assert results[0]['metadata']['page_range'] == '1'


async def test_anonymous_cannot_read_private_title_or_content(catalog):
    catalog[1]['user'] = None
    assert await find(catalog, '线粒体') == []
    assert await find(catalog, '他人') == []
    assert await find(catalog, 'TCP')


async def test_filename_without_parsed_chunks_is_searchable(catalog):
    results = await find(catalog, '期末复习')
    assert results[0]['metadata']['doc_id'] == 'title'
    assert results[0]['metadata']['filename'] == '期末复习计划.docx'


async def test_multiword_phrase_ranks_first_and_limits_document_repeats(catalog):
    results = await find(catalog, 'TCP 三次握手', top_k=50)
    assert results[0]['id'] == 'public-0'
    assert len(results) == 3
    assert await find(catalog, '根本不存在的词') == []


@pytest.mark.parametrize('query', ['100%', '_name'])
async def test_sql_wildcards_are_literal(catalog, query):
    assert [hit['id'] for hit in await find(catalog, query)] == ['literal-0']


async def test_requested_limit_and_mode_validation(catalog):
    assert len(await find(catalog, 'TCP', top_k=1)) == 1
    assert (await catalog[0].post('/api/v1/search/', json={'query': 'TCP', 'mode': 'unknown'})).status_code == 422


async def test_actual_bearer_auth_only_returns_current_accounts_private_sources(catalog):
    catalog[1]['app'].dependency_overrides.pop(get_optional_user)
    for user_id, expected in [('me', 'mine'), ('other', 'other')]:
        response = await catalog[0].post('/api/v1/search/',
            headers={'Authorization': 'Bearer ' + create_access_token(user_id)},
            json={'query': '线粒体', 'mode': 'keyword'})
        assert response.status_code == 200, response.text
        assert [hit['metadata']['doc_id'] for hit in response.json()['results']] == [expected]
    response = await catalog[0].post('/api/v1/search/', headers={'Authorization': 'Bearer invalid'},
        json={'query': '线粒体', 'mode': 'keyword'})
    assert response.json()['results'] == []
