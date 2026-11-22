"""Desktop account acceptance tests using only a disposable SQLite database.

Mount the real account routers on a minimal ASGI app. Never import app.main or
run init_db/lifespan: application migrations can rebuild a development database.
"""
from collections import defaultdict
import asyncio
from datetime import datetime, timedelta, timezone
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace

import bcrypt
from fastapi import FastAPI
import httpx
from jose import jwt
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings, settings
from app.core.auth import create_access_token, decode_access_token, hash_password
from app.database import get_db
from app.models.user import User


def load_account_router(name):
    # app.api.__init__ eagerly imports unrelated AI routers; load these files
    # directly to keep account tests independent of AI/network integrations.
    spec = importlib.util.spec_from_file_location(
        f"product_test_{name}", Path(__file__).parents[1] / f"app/api/{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


auth = load_account_router("auth")
user_api = load_account_router("user")
PASSWORD = "Study-password-2026"
ACCOUNT = {
    "username": "学习者_2026", "email": "student@example.com",
    "password": PASSWORD, "confirm_password": PASSWORD,
}


@pytest.fixture
async def account_app(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'accounts.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "jwt_secret", "isolated-product-auth-test-secret")
    monkeypatch.setattr(auth, "_rate_limit_store", defaultdict(list))
    monkeypatch.setattr(auth, "_reset_tokens", {})

    async def isolated_db():
        async with sessions() as db:
            yield db

    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(user_api.router)
    app.dependency_overrides[get_db] = isolated_db
    try:
        async with engine.begin() as connection:
            await connection.run_sync(User.__table__.create)
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 54321))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            yield SimpleNamespace(app=app, client=client, sessions=sessions)
    finally:
        await engine.dispose()


async def register(account_app, **changes):
    response = await account_app.client.post("/api/v1/auth/register", json={**ACCOUNT, **changes})
    assert response.status_code == 201, response.text
    return response.json()


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_desktop_login_setting_defaults_to_false(monkeypatch):
    monkeypatch.delenv("KNOWALL_DESKTOP_REQUIRE_LOGIN", raising=False)
    config = Settings(_env_file=None)
    assert getattr(config, "desktop_require_login", None) is False


@pytest.mark.parametrize("value, expected", [("true", True), ("1", True), ("false", False)])
def test_desktop_login_setting_reads_launcher_environment(monkeypatch, value, expected):
    monkeypatch.setenv("KNOWALL_DESKTOP_REQUIRE_LOGIN", value)
    config = Settings(_env_file=None)
    assert getattr(config, "desktop_require_login", None) is expected


@pytest.mark.parametrize("ignore_env_file, expected", [("true", "KnowAll Studio"), ("false", "project-dotenv")])
def test_cloud_worker_can_ignore_project_dotenv(tmp_path, monkeypatch, ignore_env_file, expected):
    (tmp_path / ".env").write_text("APP_NAME=project-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.setenv("JWT_SECRET", "isolated-config-test-secret")
    monkeypatch.setenv("KNOWALL_IGNORE_ENV_FILE", ignore_env_file)
    config_file = Path(__file__).resolve().parents[1] / "app/config.py"
    spec = importlib.util.spec_from_file_location("product_test_cloud_config", config_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.settings.app_name == expected


@pytest.mark.parametrize("existing_account", [False, True])
async def test_desktop_product_disables_local_session_even_on_loopback(
    account_app, monkeypatch, existing_account
):
    user = await register(account_app) if existing_account else None
    monkeypatch.setenv("KNOWALL_DESKTOP_REQUIRE_LOGIN", "true")
    config = Settings(_env_file=None, debug=True, local_auto_login=True)
    monkeypatch.setattr(auth, "settings", config)
    response = await account_app.client.post(
        "/api/v1/auth/local-session",
        json={"user_id": user["user"]["id"]} if user else {},
        headers={"x-knowall-local": "1", "origin": "http://localhost:5273"},
    )
    assert response.status_code == 200
    assert response.json() == {"enabled": False}
    async with account_app.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == int(existing_account)


async def test_register_persists_bcrypt_hash_and_issues_existing_jwt_schema(account_app):
    result = await register(account_app, username=" 学习者_2026 ", email=" Student@Example.COM ")
    assert result["token_type"] == "bearer"
    assert result["user"]["username"] == ACCOUNT["username"]
    assert result["user"]["email"] == ACCOUNT["email"]
    assert "password_hash" not in result["user"]
    token = result["access_token"]
    assert decode_access_token(token) == result["user"]["id"]
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert set(payload) == {"sub", "exp", "iat"}
    assert payload["exp"] > payload["iat"]
    async with account_app.sessions() as db:
        persisted = await db.get(User, result["user"]["id"])
        assert persisted.password_hash.startswith("$2")
        assert persisted.password_hash != PASSWORD
        assert bcrypt.checkpw(PASSWORD.encode(), persisted.password_hash.encode())
        assert persisted.is_admin is False
    profile = await account_app.client.get("/api/v1/auth/me", headers=bearer(token))
    assert profile.status_code == 200
    assert profile.json()["id"] == result["user"]["id"]


@pytest.mark.parametrize("identifier", [ACCOUNT["username"], " STUDENT@EXAMPLE.COM "])
async def test_login_accepts_username_or_normalized_email(account_app, identifier):
    registered = await register(account_app)
    response = await account_app.client.post(
        "/api/v1/auth/login", json={"username": identifier, "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["user"]["id"] == registered["user"]["id"]
    profile = await account_app.client.get(
        "/api/v1/auth/me", headers=bearer(response.json()["access_token"])
    )
    assert profile.status_code == 200
    assert profile.json()["email"] == ACCOUNT["email"]


@pytest.mark.parametrize("identifier, password", [
    (ACCOUNT["username"], "incorrect-password"), ("unknown_account", PASSWORD),
])
async def test_wrong_credentials_do_not_issue_token(account_app, identifier, password):
    await register(account_app)
    response = await account_app.client.post(
        "/api/v1/auth/login", json={"username": identifier, "password": password}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "用户名或密码错误"}


@pytest.mark.parametrize("changes, message", [
    ({"email": "other@example.com"}, "用户名已被注册"),
    ({"username": "other_student", "email": "STUDENT@EXAMPLE.COM"}, "该邮箱已被注册"),
])
async def test_duplicate_username_or_email_is_rejected(account_app, changes, message):
    await register(account_app)
    response = await account_app.client.post("/api/v1/auth/register", json={**ACCOUNT, **changes})
    assert response.status_code == 409
    assert response.json()["detail"] == message
    async with account_app.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1


@pytest.mark.parametrize("changes", [
    {"username": "!"}, {"email": "invalid-email"}, {"password": "12345"},
    {"password": "1234567", "confirm_password": "1234567"},
    {"password": "a" * 73, "confirm_password": "a" * 73},
    {"password": "学" * 25, "confirm_password": "学" * 25},
    {"confirm_password": "does-not-match"},
])
async def test_invalid_registration_does_not_create_account(account_app, changes):
    response = await account_app.client.post("/api/v1/auth/register", json={**ACCOUNT, **changes})
    assert response.status_code == 422
    async with account_app.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 0


@pytest.mark.parametrize("token_kind", ["missing", "malformed", "expired", "unknown_user"])
async def test_profile_requires_valid_token_for_existing_account(account_app, token_kind):
    registered = await register(account_app)
    headers = {}
    if token_kind == "malformed":
        headers = bearer("invalid.token")
    elif token_kind == "unknown_user":
        headers = bearer(create_access_token("missing-user"))
    elif token_kind == "expired":
        token = jwt.encode(
            {"sub": registered["user"]["id"], "exp": datetime.now(timezone.utc) - timedelta(seconds=1)},
            settings.jwt_secret, algorithm=settings.jwt_algorithm,
        )
        headers = bearer(token)
    response = await account_app.client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401


async def test_disabled_account_cannot_login_or_use_previous_token(account_app):
    registered = await register(account_app)
    async with account_app.sessions() as db:
        user = await db.get(User, registered["user"]["id"])
        user.is_active = False
        await db.commit()
    login = await account_app.client.post(
        "/api/v1/auth/login", json={"username": ACCOUNT["username"], "password": PASSWORD}
    )
    profile = await account_app.client.get(
        "/api/v1/auth/me", headers=bearer(registered["access_token"])
    )
    assert login.status_code == profile.status_code == 403


async def test_password_change_rejects_wrong_old_password_and_preserves_login(account_app):
    registered = await register(account_app)
    response = await account_app.client.put(
        "/api/v1/user/password", headers=bearer(registered["access_token"]),
        json={"old_password": "wrong-password", "new_password": "new-password-2026"},
    )
    assert response.status_code == 400
    login = await account_app.client.post(
        "/api/v1/auth/login", json={"username": ACCOUNT["username"], "password": PASSWORD}
    )
    assert login.status_code == 200


async def test_password_change_requires_token(account_app):
    response = await account_app.client.put(
        "/api/v1/user/password", json={"old_password": PASSWORD, "new_password": "new-password-2026"}
    )
    assert response.status_code == 401


async def test_password_change_persists_and_only_new_password_logs_in(account_app):
    registered = await register(account_app)
    new_password = "new-password-2026"
    response = await account_app.client.put(
        "/api/v1/user/password", headers=bearer(registered["access_token"]),
        json={"old_password": PASSWORD, "new_password": new_password},
    )
    assert response.status_code == 200
    old_login = await account_app.client.post(
        "/api/v1/auth/login", json={"username": ACCOUNT["username"], "password": PASSWORD}
    )
    new_login = await account_app.client.post(
        "/api/v1/auth/login", json={"username": ACCOUNT["username"], "password": new_password}
    )
    assert old_login.status_code == 401
    assert new_login.status_code == 200
    assert new_login.json()["user"]["id"] == registered["user"]["id"]


@pytest.mark.parametrize("new_password", ["1234567", "a" * 73, "学" * 25])
async def test_password_change_rejects_short_or_bcrypt_truncated_new_password(account_app, new_password):
    registered = await register(account_app)
    response = await account_app.client.put(
        "/api/v1/user/password", headers=bearer(registered["access_token"]),
        json={"old_password": PASSWORD, "new_password": new_password},
    )
    assert response.status_code == 400
    login = await account_app.client.post(
        "/api/v1/auth/login", json={"username": ACCOUNT["username"], "password": PASSWORD}
    )
    assert login.status_code == 200


@pytest.mark.parametrize("new_password", ["1234567", "a" * 73, "学" * 25])
async def test_password_reset_rejects_short_or_bcrypt_truncated_new_password(account_app, new_password):
    registered = await register(account_app)
    auth._reset_tokens["isolated-reset-token"] = {
        "user_id": registered["user"]["id"],
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=1),
    }
    response = await account_app.client.post(
        "/api/v1/auth/reset-password",
        json={"token": "isolated-reset-token", "new_password": new_password},
    )
    assert response.status_code == 400
    assert "isolated-reset-token" in auth._reset_tokens


@pytest.mark.parametrize("password", ["123456", "学" * 40])
async def test_existing_passwords_remain_compatible_at_login(account_app, password):
    async with account_app.sessions() as db:
        db.add(User(username="legacy_student", email="legacy@example.com", password_hash=hash_password(password)))
        await db.commit()
    response = await account_app.client.post(
        "/api/v1/auth/login", json={"username": "legacy_student", "password": password}
    )
    assert response.status_code == 200


@pytest.mark.parametrize("password", ["12345678", "学" * 24])
async def test_registration_accepts_password_policy_boundaries(account_app, password):
    registered = await register(account_app, password=password, confirm_password=password)
    response = await account_app.client.post(
        "/api/v1/auth/login", json={"username": ACCOUNT["username"], "password": password}
    )
    assert response.status_code == 200
    assert response.json()["user"]["id"] == registered["user"]["id"]


async def test_registration_never_logs_email_verification_secret(account_app, caplog):
    caplog.set_level(logging.INFO, logger="knowall.auth")
    registered = await register(account_app)
    async with account_app.sessions() as db:
        user = await db.get(User, registered["user"]["id"])
        assert user.verification_token
        assert user.verification_token not in caplog.text


async def test_resend_never_logs_email_verification_secret(account_app, caplog):
    registered = await register(account_app)
    caplog.set_level(logging.INFO, logger="knowall.auth")
    response = await account_app.client.post(
        "/api/v1/auth/resend-verification", headers=bearer(registered["access_token"])
    )
    assert response.status_code == 200
    async with account_app.sessions() as db:
        user = await db.get(User, registered["user"]["id"])
        assert user.verification_token
        assert user.verification_token not in caplog.text


async def test_password_reset_request_never_logs_reset_secret(account_app, caplog):
    registered = await register(account_app)
    caplog.set_level(logging.INFO, logger="knowall.auth")
    response = await account_app.client.post("/api/v1/auth/forgot-password", json={"email": ACCOUNT["email"]})
    assert response.status_code == 200
    assert len(auth._reset_tokens) == 1
    token, stored = next(iter(auth._reset_tokens.items()))
    assert stored["user_id"] == registered["user"]["id"]
    assert token not in caplog.text


async def test_simultaneous_duplicate_registration_returns_conflict(account_app):
    transport = httpx.ASGITransport(
        app=account_app.app, client=("127.0.0.1", 54321), raise_app_exceptions=False
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        responses = await asyncio.gather(*[
            client.post("/api/v1/auth/register", json=ACCOUNT) for _ in range(2)
        ])
    assert sorted(response.status_code for response in responses) == [201, 409]
    async with account_app.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
