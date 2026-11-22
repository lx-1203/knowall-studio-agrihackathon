"""UTC wire contract through actual JWT account routes and disposable storage."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from tests.test_product_auth import account_app, register, bearer, load_account_router, PASSWORD
from tests.test_product_auth import auth
from app.models.user import User
from app.models.user_bind import UserBind
from app.models.user_history import UserHistory
from app.models.notification import Notification

LEGACY = datetime(2026, 10, 9, 19, 40, 0, 123456)


def assert_utc(value, expected=None):
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, value
    assert parsed.utcoffset().total_seconds() == 0
    if expected is not None:
        assert parsed == expected.replace(tzinfo=timezone.utc)


async def test_register_login_me_profile_and_update_keep_one_instant(account_app):
    result = await register(account_app)
    headers = bearer(result['access_token'])
    assert_utc(result['user']['created_at'])
    async with account_app.sessions() as db:
        stored = await db.get(User, result['user']['id'])
        before = stored.created_at
        assert before.tzinfo is None
    for identifier in ('学习者_2026', 'student@example.com'):
        response = await account_app.client.post('/api/v1/auth/login', json={'username': identifier, 'password': PASSWORD})
        assert response.status_code == 200, response.text
        assert_utc(response.json()['user']['created_at'], before)
    for path in ('/api/v1/auth/me', '/api/v1/user/profile'):
        response = await account_app.client.get(path, headers=headers)
        assert response.status_code == 200
        assert_utc(response.json()['created_at'], before)
    updated = await account_app.client.put('/api/v1/user/profile', headers=headers, json={'nickname': '本地时间验证'})
    assert updated.status_code == 200, updated.text
    assert_utc(updated.json()['created_at'], before)
    assert_utc(updated.json()['updated_at'])
    async with account_app.sessions() as db:
        assert (await db.get(User, result['user']['id'])).created_at == before


async def test_legacy_user_and_private_records_have_explicit_utc_without_migration(account_app):
    result = await register(account_app)
    other = await register(account_app, username='第二学习者', email='other@example.com')
    account_app.app.include_router(load_account_router('notifications').router)
    async with account_app.sessions() as db:
        async with db.bind.begin() as connection:
            for model in (UserBind, UserHistory, Notification):
                await connection.run_sync(model.__table__.create)
        user = await db.get(User, result['user']['id'])
        user.created_at = LEGACY
        db.add_all([
            UserBind(user_id=user.id, provider='github', provider_name='GitHub', bound_at=LEGACY),
            UserHistory(user_id=user.id, action_type='test', action_label='时间验证', created_at=LEGACY),
            Notification(user_id=user.id, title='时间通知', created_at=LEGACY),
        ])
        await db.commit()
    headers = bearer(result['access_token'])
    for path in ('/api/v1/auth/me', '/api/v1/user/profile'):
        response = await account_app.client.get(path, headers=headers)
        assert_utc(response.json()['created_at'], LEGACY)
    binds = (await account_app.client.get('/api/v1/user/binds', headers=headers)).json()
    assert_utc(next(b for b in binds if b['provider'] == 'github')['bound_at'], LEGACY)
    assert all(b['bound_at'] is None for b in binds if not b['is_bound'])
    for path in ('/api/v1/user/history', '/api/v1/notifications'):
        response = await account_app.client.get(path, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()['total'] == 1
        assert_utc(response.json()['items'][0]['created_at'], LEGACY)
        isolated = await account_app.client.get(path, headers=bearer(other['access_token']))
        assert isolated.json()['items'] == []
        assert (await account_app.client.get(path)).status_code == 401
    async with account_app.sessions() as db:
        assert (await db.get(User, result['user']['id'])).created_at == LEGACY
        assert (await db.scalar(select(Notification))).created_at == LEGACY


@pytest.mark.parametrize('offset', [0, 8, -4])
def test_native_serialization_preserves_aware_instant(offset):
    from datetime import timedelta
    from app.core.utc_time import utc_iso
    instant = datetime(2026, 10, 9, 19, 40, tzinfo=timezone.utc)
    assert datetime.fromisoformat(utc_iso(instant.astimezone(timezone(timedelta(hours=offset))))) == instant


async def test_opt_in_local_session_uses_the_same_wire_contract(account_app, monkeypatch):
    from app.config import Settings
    monkeypatch.setattr(auth, 'settings', Settings(_env_file=None, debug=True, local_auto_login=True, desktop_require_login=False))
    response = await account_app.client.post('/api/v1/auth/local-session', json={}, headers={'x-knowall-local': '1', 'origin': 'http://localhost:5273'})
    assert response.status_code == 200, response.text
    assert response.json()['enabled'] is True
    assert_utc(response.json()['user']['created_at'])


async def test_null_optional_update_time_is_preserved(account_app):
    from sqlalchemy import text
    result = await register(account_app)
    async with account_app.sessions() as db:
        await db.execute(text('UPDATE users SET updated_at = NULL WHERE id = :id'), {'id': result['user']['id']})
        await db.commit()
    for path in ('/api/v1/auth/me', '/api/v1/user/profile'):
        response = await account_app.client.get(path, headers=bearer(result['access_token']))
        assert response.status_code == 200
        assert response.json()['updated_at'] is None

