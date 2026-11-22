"""Reading terminal events and vocabulary generation with real isolated persistence."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import language, reading
from app.core.auth import get_optional_user
from app.database import Base, get_db
from app.models import Document, KnowledgeSummary


@pytest.fixture
async def flow(monkeypatch):
    import app.database
    from app.core.api_scheduler import api_client
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(app.database, 'async_session', factory)
    monkeypatch.setattr(language, 'load_user_api_keys', AsyncMock())
    model = AsyncMock(return_value=SimpleNamespace(content=json.dumps([
        {'word': 'energy', 'definition': '能量', 'part_of_speech': 'noun'}])))
    monkeypatch.setattr(api_client, 'generate', model)
    monkeypatch.setattr(reading, 'DEEPSEEK_API_KEY', 'controlled-test-key')
    provider_calls = []
    async def stream(text, known, target):
        provider_calls.append((text, known))
        yield 'data: ' + json.dumps({'token': 'partial'}) + '\n\n'
        yield 'data: ' + json.dumps({'done': True, 'result': '完成 energy', 'vocabulary': [{'cn': '能量', 'en': 'energy'}]}) + '\n\n'
    monkeypatch.setattr(reading, 'stream_deepseek', stream)
    async with factory() as db:
        for doc in ['biology', 'other', 'empty']:
            db.add(Document(id=doc, filename=doc+'.txt', file_type='txt', sha256=doc))
        db.add(KnowledgeSummary(id='summary', document_id='biology', document_ids=['biology'], content_md='# Energy\nEnergy is important.'))
        db.add(KnowledgeSummary(id='other-summary', document_id='other', document_ids=['other'], content_md='# Other\nOther subject.'))
        await db.commit()
    async def database():
        async with factory() as db:
            yield db
    app = FastAPI()
    app.include_router(reading.router)
    app.include_router(language.router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_optional_user] = lambda: SimpleNamespace(id='learner')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://study') as client:
        yield client, model, provider_calls
    await engine.dispose()


def events(response):
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


async def test_stream_cache_refresh_and_learning_state_are_distinct(flow):
    client, _, calls = flow
    route = '/api/v1/reading/convert/stream'
    req = {'text': '学习能量帮助我们理解科学世界。', 'level': 1}
    assert events(await client.post(route, json=req))[-1]['result'] == '完成 energy'
    assert events(await client.post(route, json=req))[-1]['cached'] is True
    assert len(calls) == 1
    await client.post(route, json={**req, 'skip_cache': True})
    assert len(calls) == 2
    await client.post(route, json={**req, 'seen_words': [{'cn': '能量', 'en': 'energy'}]})
    assert len(calls) == 3
    await client.post(route, json={**req, 'level': 3})
    assert len(calls) == 4


async def test_no_key_uses_dictionary_without_contacting_provider(flow, monkeypatch):
    client, _, calls = flow
    monkeypatch.setattr(reading, 'DEEPSEEK_API_KEY', '')
    event = events(await client.post('/api/v1/reading/convert/stream', json={'text': '我们的世界很大，让我们去看看。', 'level': 3}))[-1]
    assert event['done'] and event['source'] == 'dictionary'
    assert event['result'] and calls == []


async def test_provider_failure_emits_completed_dictionary_result(flow, monkeypatch):
    client, _, _ = flow
    async def broken(*args):
        yield 'data: {"token":"partial"}\n\n'
        raise RuntimeError('controlled outage')
    monkeypatch.setattr(reading, 'stream_deepseek', broken)
    result = events(await client.post('/api/v1/reading/convert/stream', json={'text': '我们的世界很大，让我们去看看。'}))[-1]
    assert result['done'] and result['source'] == 'dictionary'
    assert result['result'] != 'partial'


async def test_generation_returns_persistent_ids_and_reuses_mastered_words(flow):
    client, model, _ = flow
    route = '/api/v1/language/vocabulary'
    req = {'document_id': 'biology', 'summary_id': 'summary'}
    first = await client.post(route+'/generate', json=req)
    assert first.status_code == 200, first.text
    word = first.json()['result']['words'][0]
    assert isinstance(word['id'], str) and word['id']
    assert (await client.patch(route+'/'+word['id'], json={'mastered': True})).status_code == 200
    second = await client.post(route+'/generate', json=req)
    assert second.status_code == 200, second.text
    assert second.json()['result']['words'][0]['id'] == word['id']
    saved = (await client.get(route, params={'document_id': 'biology'})).json()
    assert saved['total'] == 1 and saved['words'][0]['mastered'] is True
    assert model.await_count == 2


async def test_optional_summary_resolves_existing_document_source(flow):
    client, model, _ = flow
    result = await client.post('/api/v1/language/vocabulary/generate', json={'document_id': 'biology'})
    assert result.status_code == 200, result.text
    assert result.json()['result']['total_words'] == 1
    model.assert_awaited_once()


@pytest.mark.parametrize('req,status', [
    ({'document_id': 'biology', 'summary_id': 'other-summary'}, 400),
    ({'document_id': 'missing', 'summary_id': 'summary'}, 404),
    ({'document_id': 'biology', 'summary_id': 'missing'}, 404),
    ({'document_id': 'empty'}, 400),
])
async def test_invalid_source_rejected_before_model_call(flow, req, status):
    client, model, _ = flow
    result = await client.post('/api/v1/language/vocabulary/generate', json=req)
    assert result.status_code == status, result.text
    model.assert_not_awaited()


@pytest.mark.parametrize('payload', ['not JSON', '{}', '[{"word":"energy"}, 1]', '[{"word": 1}]'])
async def test_invalid_generation_is_atomic_and_visible(flow, payload):
    client, model, _ = flow
    model.return_value.content = payload
    result = await client.post('/api/v1/language/vocabulary/generate', json={'document_id': 'biology', 'summary_id': 'summary'})
    assert result.status_code == 500, result.text
    assert (await client.get('/api/v1/language/vocabulary')).json()['total'] == 0


async def test_duplicate_model_words_create_one_persistent_item(flow):
    client, model, _ = flow
    model.return_value.content = '[{"word":"energy","definition":"能量"},{"word":" energy ","definition":"能量"}]'
    result = await client.post('/api/v1/language/vocabulary/generate', json={'document_id': 'biology', 'summary_id': 'summary'})
    assert result.status_code == 200, result.text
    assert result.json()['result']['total_words'] == 1
    assert (await client.get('/api/v1/language/vocabulary')).json()['total'] == 1


async def test_non_streaming_cache_respects_seen_level_and_explicit_refresh(flow, monkeypatch):
    client, _, _ = flow
    provider = AsyncMock(return_value=('完成 energy', [{'cn': '能量', 'en': 'energy'}]))
    monkeypatch.setattr(reading, 'call_deepseek', provider)
    req = {'text': '学习能量帮助我们理解科学世界。'}
    route = '/api/v1/reading/convert'
    assert (await client.post(route, json=req)).json()['source'] == 'deepseek'
    assert (await client.post(route, json=req)).json()['source'] == 'cache'
    assert provider.await_count == 1
    for extra in [{'seen_words': [{'cn': '能量', 'en': 'energy'}]}, {'level': 3}, {'skip_cache': True}]:
        assert (await client.post(route, json={**req, **extra})).json()['source'] == 'deepseek'
    assert provider.await_count == 4


async def test_omitted_summary_chooses_latest_matching_source(flow):
    import app.database
    from datetime import datetime
    client, model, _ = flow
    async with app.database.async_session() as db:
        db.add(KnowledgeSummary(id='latest', document_id='biology', document_ids=['biology'],
            content_md='# Latest marker\nLatest source content.', created_at=datetime(2099, 1, 1)))
        await db.commit()
    assert (await client.post('/api/v1/language/vocabulary/generate', json={'document_id': 'biology'})).status_code == 200
    assert 'Latest marker' in model.call_args.kwargs['generation_content']


async def test_combined_summary_accepts_secondary_source_document(flow):
    import app.database
    client, model, _ = flow
    async with app.database.async_session() as db:
        db.add(KnowledgeSummary(id='combined', document_id='other', document_ids=['other', 'biology'], content_md='# Combined\nEnergy is important.'))
        await db.commit()
    result = await client.post('/api/v1/language/vocabulary/generate', json={'document_id': 'biology', 'summary_id': 'combined'})
    assert result.status_code == 200, result.text
    assert result.json()['result']['total_words'] == 1
    model.assert_awaited_once()
