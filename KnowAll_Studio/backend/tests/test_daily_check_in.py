"""Daily check-in persistence, ownership, time boundary and duplicate protection."""
import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.core.auth import get_current_user
from app.models.user import User


@pytest.fixture
async def checkins(tmp_path, monkeypatch):
    from app.api import check_ins as routes
    from app.models.gamification import DailyCheckIn
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path/'check-in.sqlite').as_posix(), connect_args={'timeout': 20})
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[User.__table__, DailyCheckIn.__table__]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all([User(id=uid, username=uid, email=uid+'@example.com', password_hash='unused') for uid in ['alice', 'bob']])
        await db.commit()
    clock = {'now': datetime(2026, 10, 17, 8, tzinfo=timezone.utc)}
    monkeypatch.setattr(routes, 'current_instant', lambda: clock['now'])
    app = FastAPI(); app.include_router(routes.router)
    async def database():
        async with factory() as db:
            yield db
    account = {'id': 'alice'}
    app.dependency_overrides[get_db] = database
    async def authenticated(db: AsyncSession = Depends(get_db)):
        return await db.get(User, account['id'])
    app.dependency_overrides[get_current_user] = authenticated
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield SimpleNamespace(client=client, factory=factory, clock=clock, account=account, app=app)
    await engine.dispose()


async def test_empty_state_is_read_only(checkins):
    from app.models.gamification import DailyCheckIn
    result = await checkins.client.get('/api/v1/check-ins')
    assert result.status_code == 200
    data = result.json()
    assert data['today'] == '2026-10-17' and data['timezone'] == 'Asia/Shanghai'
    assert not data['checked_in_today'] and data['total_days'] == data['current_streak'] == 0
    assert data['records'] == []
    async with checkins.factory() as db:
        assert await db.scalar(select(func.count()).select_from(DailyCheckIn)) == 0


async def test_check_in_persists_once_and_does_not_replace_original_note(checkins):
    result = await checkins.client.post('/api/v1/check-ins', json={'note': '  复习了细胞结构  '})
    assert result.status_code == 200
    first = result.json()
    assert first['checked_in_today'] and first['total_days'] == first['current_streak'] == first['longest_streak'] == 1
    assert first['records'][0]['note'] == '复习了细胞结构'
    assert first['records'][0]['checked_at'].endswith('+00:00')
    repeated = (await checkins.client.post('/api/v1/check-ins', json={'note': '不应覆盖'})).json()
    assert repeated['already_checked_in'] and repeated['records'][0] == first['records'][0]
    reloaded = (await checkins.client.get('/api/v1/check-ins')).json()
    assert reloaded['records'] == first['records'] and reloaded['total_days'] == 1


async def test_concurrent_repeated_taps_create_one_record(checkins):
    from app.models.gamification import DailyCheckIn
    replies = await asyncio.gather(*[checkins.client.post('/api/v1/check-ins', json={}) for _ in range(4)])
    assert all(reply.status_code == 200 for reply in replies)
    assert sum(not reply.json()['already_checked_in'] for reply in replies) == 1
    async with checkins.factory() as db:
        assert await db.scalar(select(func.count()).select_from(DailyCheckIn)) == 1


async def test_accounts_are_isolated(checkins):
    await checkins.client.post('/api/v1/check-ins', json={'note': '私人的心得'})
    checkins.account['id'] = 'bob'
    assert (await checkins.client.get('/api/v1/check-ins')).json()['total_days'] == 0
    own = (await checkins.client.post('/api/v1/check-ins', json={})).json()
    assert own['total_days'] == 1 and own['records'][0]['note'] == ''


async def test_beijing_day_changes_at_utc_1600(checkins):
    checkins.clock['now'] = datetime(2026, 10, 17, 15, 59, 59, tzinfo=timezone.utc)
    assert (await checkins.client.post('/api/v1/check-ins', json={})).json()['today'] == '2026-10-17'
    checkins.clock['now'] = datetime(2026, 10, 17, 16, tzinfo=timezone.utc)
    before = (await checkins.client.get('/api/v1/check-ins')).json()
    assert before['today'] == '2026-10-18' and not before['checked_in_today'] and before['current_streak'] == 1
    after = (await checkins.client.post('/api/v1/check-ins', json={})).json()
    assert after['current_streak'] == after['total_days'] == 2


async def test_streaks_and_month_records_cross_month_and_leap_day(checkins):
    from app.models.gamification import DailyCheckIn
    checkins.clock['now'] = datetime(2024, 3, 2, 8, tzinfo=timezone.utc)
    async with checkins.factory() as db:
        for day in ['2024-02-25', '2024-02-26', '2024-02-28', '2024-02-29', '2024-03-01']:
            db.add(DailyCheckIn(user_id='alice', check_in_date=date.fromisoformat(day), note=''))
        await db.commit()
    data = (await checkins.client.get('/api/v1/check-ins', params={'month': '2024-02'})).json()
    assert data['current_streak'] == data['longest_streak'] == 3 and data['total_days'] == 5
    assert len(data['records']) == 4 and data['month_days'] == 29
    checkins.clock['now'] = datetime(2024, 3, 3, 8, tzinfo=timezone.utc)
    assert (await checkins.client.get('/api/v1/check-ins')).json()['current_streak'] == 0


async def test_invalid_month_date_override_and_long_note_rejected(checkins):
    assert (await checkins.client.get('/api/v1/check-ins', params={'month': '2026-13'})).status_code == 422
    assert (await checkins.client.post('/api/v1/check-ins', json={'date': '2000-01-01'})).status_code == 422
    assert (await checkins.client.post('/api/v1/check-ins', json={'note': 'x'*301})).status_code == 422


async def test_authentication_required(checkins):
    checkins.app.dependency_overrides.pop(get_current_user)
    assert (await checkins.client.get('/api/v1/check-ins')).status_code == 401
    assert (await checkins.client.post('/api/v1/check-ins', json={})).status_code == 401


def test_additive_migration_keeps_existing_users(tmp_path):
    import importlib.util
    from pathlib import Path
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).parents[1]/'alembic/versions/20261017d001_add_daily_check_ins.py'
    spec = importlib.util.spec_from_file_location('daily_check_in_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine('sqlite:///' + (tmp_path/'migration.sqlite').as_posix())
    try:
        with engine.begin() as connection:
            User.__table__.create(connection)
            connection.execute(User.__table__.insert(), {'id': 'alice', 'username': 'alice', 'email': 'alice@example.com', 'password_hash': 'unused'})
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
            assert connection.scalar(sa.select(sa.func.count()).select_from(User.__table__)) == 1
            constraints = sa.inspect(connection).get_unique_constraints('daily_check_ins')
            assert any(item['column_names'] == ['user_id', 'check_in_date'] for item in constraints)
    finally:
        engine.dispose()
