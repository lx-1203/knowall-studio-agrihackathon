"""Persistent reading, vocabulary and dictation flows without external models."""
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import language, reading, recitation
from app.core.auth import get_optional_user
from app.database import Base, get_db
from app.models import Document, LanguageVocabulary, RecitationRecord


@pytest.fixture
async def study():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(Document(id='biology', filename='biology.txt', file_type='txt', sha256='a'*64))
        db.add(LanguageVocabulary(id='word-energy', document_id='biology', word='energy', definition='能量', difficulty='easy'))
        await db.commit()
    async def database():
        async with factory() as db:
            yield db
    owner = SimpleNamespace(id='learner')
    app = FastAPI()
    for router in [reading.router, language.router, recitation.router]:
        app.include_router(router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_optional_user] = lambda: owner
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://study') as client:
        yield client, factory, owner
    await engine.dispose()


async def test_article_upload_read_delete_and_missing_result(study):
    client, _, _ = study
    content = '学习需要坚持每天练习，通过阅读理解新的知识并及时复习才能逐步掌握内容。'
    response = await client.post('/api/v1/reading/articles/upload', json={'title': '练习文章', 'content': content})
    assert response.status_code == 200, response.text
    article = response.json()['article']
    assert article['content'] == content
    assert any(a['id'] == article['id'] for a in (await client.get('/api/v1/reading/articles')).json())
    assert (await client.get('/api/v1/reading/articles/' + article['id'])).json()['content'] == content
    assert (await client.delete('/api/v1/reading/articles/' + article['id'])).status_code == 200
    assert (await client.get('/api/v1/reading/articles/' + article['id'])).status_code == 404


@pytest.mark.parametrize('identifier', ['u', 'uabc', 'u-1'])
async def test_invalid_uploaded_article_identifier_is_a_client_error(study, identifier):
    client, _, _ = study
    for method in [client.get, client.delete]:
        response = await method('/api/v1/reading/articles/' + identifier)
        assert response.status_code in {400, 404}, response.text


async def test_vocabulary_mastery_persists_and_filter_reflects_changes(study):
    client, _, _ = study
    route = '/api/v1/language/vocabulary'
    assert (await client.get(route, params={'mastered': False})).json()['total'] == 1
    response = await client.patch(route + '/word-energy', json={'mastered': True})
    assert response.status_code == 200
    assert (await client.get(route, params={'mastered': False})).json()['total'] == 0
    mastered = (await client.get(route, params={'mastered': True})).json()
    assert mastered['words'][0]['word'] == 'energy'
    assert mastered['words'][0]['mastered'] is True
    assert (await client.patch(route + '/missing', json={'mastered': True})).status_code == 404


async def test_dictation_review_statistics_and_owner_boundary(study):
    client, factory, owner = study
    created = await client.post('/api/v1/recitation/passages', json={'title': '静夜思', 'content': '床前明月光，疑是地上霜。'})
    assert created.status_code == 200
    passage = created.json()
    correct = await client.post('/api/v1/recitation/dictation/submit', json={
        'passage_id': passage['id'], 'user_input': passage['content'], 'time_spent_ms': 1200})
    assert correct.status_code == 200 and correct.json()['accuracy'] == 1
    record_id = correct.json()['record_id']
    review = await client.post('/api/v1/recitation/review', json={'record_id': record_id, 'rating': 3})
    assert review.status_code == 200, review.text
    assert review.json()['review_count'] == 1 and review.json()['next_review_at']
    async with factory() as db:
        saved = await db.get(RecitationRecord, record_id)
        assert saved.user_input == passage['content'] and saved.review_count == 1
    stats = (await client.get('/api/v1/recitation/stats')).json()
    assert stats['total_passages'] == 1 and stats['total_records'] == 1 and stats['avg_accuracy'] == 1
    owner.id = 'other'
    assert (await client.get('/api/v1/recitation/passages/' + passage['id'])).status_code == 404
    assert (await client.post('/api/v1/recitation/review', json={'record_id': record_id, 'rating': 4})).status_code == 404
    assert (await client.get('/api/v1/recitation/stats')).json()['total_records'] == 0
    owner.id = 'learner'
    incorrect = await client.post('/api/v1/recitation/dictation/submit', json={'passage_id': passage['id'], 'user_input': '床前明月'})
    assert 0 < incorrect.json()['accuracy'] < 1 and incorrect.json()['missing_chars'] > 0
    assert (await client.delete('/api/v1/recitation/passages/' + passage['id'])).status_code == 200
    assert (await client.get('/api/v1/recitation/passages/' + passage['id'])).status_code == 404


@pytest.mark.parametrize('content', ['', '   \n\t'])
async def test_blank_passage_cannot_become_a_practice_resource(study, content):
    client, _, _ = study
    response = await client.post('/api/v1/recitation/passages', json={'title': '空段落', 'content': content})
    assert response.status_code in {400, 422}
    assert (await client.get('/api/v1/recitation/passages')).json()['passages'] == []
