"""Real summary route and native Query validation, no API/model calls or live DB."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import get_db
from app.models import APICallLog
from tests.test_product_auth import load_account_router

admin = load_account_router('admin')
BASE = datetime(2026, 10, 9, 19, 40)


@pytest.fixture
async def logs_app(tmp_path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "logs.db"}')
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def db():
        async with sessions() as session:
            yield session
    app = FastAPI(); app.include_router(admin.router); app.dependency_overrides[get_db] = db
    async with engine.begin() as connection:
        await connection.run_sync(APICallLog.__table__.create)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            yield SimpleNamespace(client=client, sessions=sessions)
    finally:
        await engine.dispose()


async def seed(logs_app, count=72):
    async with logs_app.sessions() as db:
        db.add_all([APICallLog(id=f'log-{i}', user_id='synthetic', task_type='chat', model_name=f'model-{i}', created_at=BASE+timedelta(seconds=i), tokens_input=0, tokens_output=3, cost_estimate=0, success=i%2==0, from_cache=i%3==0, duration_ms=42, error_message='private error detail', content_summary='private source detail') for i in range(count)])
        await db.commit()


async def test_old_storage_emits_explicit_utc_preserving_fields_and_instant(logs_app):
    await seed(logs_app, 1)
    response = await logs_app.client.get('/api/v1/admin/logs')
    assert response.status_code == 200
    item = response.json()[0]
    parsed = datetime.fromisoformat(item['created_at'])
    assert parsed.tzinfo is not None, item['created_at']
    assert parsed == BASE.replace(tzinfo=timezone.utc)
    assert set(item) == {'id', 'task_type', 'model_name', 'tokens_input', 'tokens_output', 'cost_estimate', 'from_cache', 'success', 'duration_ms', 'created_at'}
    assert item['tokens_input'] == 0 and item['from_cache'] is True and item['success'] is True
    async with logs_app.sessions() as db:
        assert (await db.scalar(select(APICallLog))).created_at == BASE


@pytest.mark.parametrize('limit', ['-1', '0', '501', '100000', '1.5', 'bad'])
async def test_invalid_limit_rejected_instead_of_unbounded_or_empty_query(logs_app, limit):
    await seed(logs_app)
    response = await logs_app.client.get('/api/v1/admin/logs', params={'limit': limit})
    assert response.status_code == 422, response.text


@pytest.mark.parametrize('limit,expected', [(None, 50), (1, 1), (20, 20), (500, 72)])
async def test_compatible_limit_newest_first_and_summary_only(logs_app, limit, expected):
    await seed(logs_app)
    response = await logs_app.client.get('/api/v1/admin/logs', params={} if limit is None else {'limit': limit})
    assert response.status_code == 200
    items = response.json(); assert len(items) == expected
    assert items[0]['id'] == 'log-71'
    assert 'private' not in response.text
    assert all(items[i]['created_at'] >= items[i+1]['created_at'] for i in range(len(items)-1))


async def test_empty_workspace_is_empty_summary(logs_app):
    assert (await logs_app.client.get('/api/v1/admin/logs')).json() == []
