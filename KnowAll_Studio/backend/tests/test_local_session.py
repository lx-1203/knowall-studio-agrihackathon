from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from fastapi import HTTPException
from starlette.requests import Request
import importlib.util
from pathlib import Path
_spec = importlib.util.spec_from_file_location('local_auth_test', Path(__file__).parents[1] / 'app/api/auth.py')
auth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(auth)
from app.core.auth import decode_access_token


def request(host='127.0.0.1', url_host='localhost', origin='http://localhost:5273', marker='1'):
    headers = [(b'host', url_host.encode())]
    if origin is not None:
        headers.append((b'origin', origin.encode()))
    if marker is not None:
        headers.append((b'x-knowall-local', marker.encode()))
    return Request({'type': 'http', 'method': 'POST', 'scheme': 'http', 'path': '/', 'headers': headers, 'client': (host, 1234), 'server': ('127.0.0.1', 8001), 'query_string': b''})


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(auth.settings, 'local_auto_login', True)
    monkeypatch.setattr(auth.settings, 'debug', True)


@pytest.fixture
def db():
    db = AsyncMock()
    db.add = MagicMock()
    db.get.return_value = None
    return db


@pytest.mark.asyncio
async def test_disabled_never_reads_db(monkeypatch, db):
    monkeypatch.setattr(auth.settings, 'local_auto_login', False)
    assert await auth.local_session(auth.LocalSessionRequest(), request(), db) == {'enabled': False}
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('kwargs', [
    {'host': '192.168.1.5'}, {'url_host': 'evil.example'},
    {'origin': 'https://evil.example'}, {'origin': 'null'}, {'marker': None},
])
async def test_rejects_remote_and_cross_site(enabled, db, kwargs):
    with pytest.raises(HTTPException) as error:
        await auth.local_session(auth.LocalSessionRequest(), request(**kwargs), db)
    assert error.value.status_code == 403
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_production_debug_off_disables_session(enabled, monkeypatch, db):
    monkeypatch.setattr(auth.settings, 'debug', False)
    assert await auth.local_session(auth.LocalSessionRequest(), request(), db) == {'enabled': False}
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_reuses_preferred_account_and_issues_valid_jwt(enabled, db):
    user = SimpleNamespace(id='existing', username='旧账号', email='old@example.com', is_active=True, created_at=datetime(2026, 1, 1))
    db.get.return_value = user
    result = await auth.local_session(auth.LocalSessionRequest(user_id='existing'), request(), db)
    assert result['enabled'] is True
    assert result['user']['id'] == 'existing'
    assert decode_access_token(result['access_token']) == 'existing'
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_preferred_account_is_not_reactivated(enabled, db):
    db.get.return_value = SimpleNamespace(is_active=False)
    with pytest.raises(HTTPException) as error:
        await auth.local_session(auth.LocalSessionRequest(user_id='disabled'), request(), db)
    assert error.value.status_code == 403
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_database_creates_local_account(enabled, db):
    db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    async def refresh(user):
        user.created_at = datetime(2026, 1, 1)
    db.refresh.side_effect = refresh
    result = await auth.local_session(auth.LocalSessionRequest(), request(), db)
    assert result['user']['id'] == 'local_user'
    assert decode_access_token(result['access_token']) == 'local_user'
    db.add.assert_called_once()
    assert db.add.call_args.args[0].is_admin is False



@pytest.mark.asyncio
async def test_real_database_account_is_persistent_and_token_authorizes_profile(enabled, tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.database import Base
    from app.models.user import User
    from fastapi.security import HTTPAuthorizationCredentials
    from app.core.auth import get_current_user
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'local.db'))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session_db:
            first = await auth.local_session(auth.LocalSessionRequest(), request(), session_db)
        async with factory() as session_db:
            second = await auth.local_session(auth.LocalSessionRequest(), request(), session_db)
            assert first['user']['id'] == second['user']['id'] == 'local_user'
            user = await get_current_user(HTTPAuthorizationCredentials(scheme='Bearer', credentials=second['access_token']), session_db)
            assert user.username == '本地用户'
    finally:
        await engine.dispose()

@pytest.mark.asyncio
async def test_malformed_origin_is_rejected(enabled, db):
    with pytest.raises(HTTPException) as error:
        await auth.local_session(auth.LocalSessionRequest(), request(origin='http://[broken'), db)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_two_fresh_tabs_share_one_account(enabled, tmp_path):
    import asyncio
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.database import Base
    engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'concurrent.db'))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async def bootstrap():
            async with factory() as session_db:
                return await auth.local_session(auth.LocalSessionRequest(), request(), session_db)
        sessions = await asyncio.gather(bootstrap(), bootstrap())
        assert all(session['user']['id'] == 'local_user' for session in sessions)
    finally:
        await engine.dispose()
