"""Console contracts exercised with real JWTs and disposable SQLite storage only."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.core.auth import create_access_token
from app.database import Base, get_db
from app.models import (APICallLog, APICache, APIKey, APIQuota, AnswerRecord, Deck, Document, DocumentChunk,
                        ExamPaper, Flashcard, QuestionBank, ReviewLog, User)
from app.models.system_config import SystemConfig

PREFIX = "/api/v1/admin/console"


@pytest.fixture
async def console_app(tmp_path, monkeypatch):
    source = Path(__file__).parents[1] / "app/api/admin_console.py"
    assert source.exists(), "console API must be implemented"
    spec = importlib.util.spec_from_file_location("isolated_admin_console", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'console.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(settings, "jwt_secret", "isolated-admin-console-secret")

    async def isolated_db():
        async with sessions() as db:
            yield db

    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[get_db] = isolated_db
    try:
        async with engine.begin() as conn:
            tables = [model.__table__ for model in (User, Document, DocumentChunk, QuestionBank,
                      Deck, Flashcard, ReviewLog, ExamPaper, AnswerRecord, APICallLog, APIKey, APIQuota, APICache, SystemConfig)]
            await conn.run_sync(lambda connection: Base.metadata.create_all(connection, tables=tables))
        async with sessions() as db:
            db.add_all([
                User(id="admin", username="root", email="root@example.com", password_hash="private", is_admin=True),
                User(id="student", username="learner", email="student@example.com", password_hash="private"),
            ])
            await db.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield SimpleNamespace(client=client, sessions=sessions, module=module)
    finally:
        await engine.dispose()


def headers(user="admin"):
    return {"Authorization": f"Bearer {create_access_token(user)}"}


async def test_access_uses_real_user_role_and_revocation(console_app):
    client = console_app.client
    assert (await client.get(PREFIX + "/access")).status_code == 401
    assert (await client.get(PREFIX + "/access", headers=headers("student"))).json() == {"is_admin": False}
    assert (await client.get(PREFIX + "/access", headers=headers())).json() == {"is_admin": True}
    async with console_app.sessions() as db:
        (await db.get(User, "admin")).is_admin = False
        await db.commit()
    assert (await client.get(PREFIX + "/overview", headers=headers())).status_code == 403


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/overview", None), ("GET", "/users", None),
    ("PATCH", "/users/student", {"is_active": False}),
    ("GET", "/documents", None), ("GET", "/documents/missing", None),
    ("PATCH", "/documents/missing", {"filename": "x"}),
    ("GET", "/questions", None), ("PATCH", "/questions/missing", {"answer": "x"}),
    ("GET", "/health", None), ("GET", "/logs", None), ("GET", "/keys", None),
    ("POST", "/keys", {"provider": "openai", "api_key": "secret"}),
    ("DELETE", "/keys/missing", None), ("PUT", "/keys/missing/toggle", None),
    ("GET", "/models", None), ("POST", "/models", {"model": "deepseek-chat"}),
    ("GET", "/cache/stats", None), ("DELETE", "/cache/clear", None), ("GET", "/quota/status", None),
])
async def test_all_console_routes_enforce_authentication_and_admin(console_app, method, path, body):
    for auth_headers, status in (({}, 401), (headers("student"), 403)):
        response = await console_app.client.request(method, PREFIX + path, json=body, headers=auth_headers)
        assert response.status_code == status, response.text


async def test_overview_aggregates_full_period_not_latest_500(console_app):
    today = datetime.now(timezone.utc).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)
    async with console_app.sessions() as db:
        db.add(Document(id="doc", filename="课件.pdf", file_type="pdf", sha256="a" * 64))
        db.add(QuestionBank(id="q", question_type="single_choice", question_text="问题", correct_answer="A"))
        db.add(Flashcard(id="f", card_type="qa", front="a", back="b"))
        await db.flush()
        db.add(ReviewLog(card_id="f", rating=3))
        db.add_all([AnswerRecord(question_id="q", is_correct=value, answered_at=today) for value in (True, False, True)])
        db.add(AnswerRecord(question_id="q", is_correct=True, answered_at=today - timedelta(days=90)))
        db.add_all([APICallLog(task_type="chat", model_name="model-a", tokens_input=2, tokens_output=3,
                               cost_estimate=0.01, duration_ms=100, success=i % 2 == 0, created_at=today)
                    for i in range(501)])
        db.add(APICallLog(task_type="old", model_name="old", tokens_input=999, created_at=today - timedelta(days=7)))
        db.add(APICallLog(task_type="future", model_name="future", created_at=today + timedelta(days=1)))
        await db.commit()
    response = await console_app.client.get(PREFIX + "/overview?days=7", headers=headers())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["totals"] == dict(users=2, active_users=2, documents=1, questions=1, flashcards=1, reviews=1)
    assert data["period"] == dict(days=7, calls=501, successes=251, tokens=2505, cost=pytest.approx(5.01), avg_duration_ms=100)
    assert len(data["daily"]) == 7
    assert data["daily"][-1] == dict(date=today.date().isoformat(), calls=501, successes=251, tokens=2505)
    assert data["models"] == [dict(model="model-a", calls=501, tokens=2505)]
    assert data["document_types"] == [dict(type="pdf", count=1)]
    assert data["learning"] == dict(attempts=4, correct=3, accuracy=75)
    assert datetime.fromisoformat(data["generated_at"]).utcoffset().total_seconds() == 0
    assert (await console_app.client.get(PREFIX + "/overview?days=8", headers=headers())).status_code == 422
    month = (await console_app.client.get(PREFIX + "/overview?days=30", headers=headers())).json()
    assert len(month["daily"]) == 30 and month["period"]["calls"] == 502
    assert month["learning"] == data["learning"]


async def test_user_pagination_updates_and_self_disable_protection(console_app):
    client = console_app.client
    result = (await client.get(PREFIX + "/users?search=learner&page_size=1", headers=headers())).json()
    assert result["total"] == 1 and result["page"] == 1 and result["page_size"] == 1
    assert result["items"][0]["id"] == "student"
    assert "password_hash" not in result["items"][0]
    assert (await client.patch(PREFIX + "/users/admin", headers=headers(), json={"is_active": False})).status_code == 400
    response = await client.patch(PREFIX + "/users/student", headers=headers(), json={"is_active": False})
    assert response.status_code == 200 and response.json()["is_active"] is False
    async with console_app.sessions() as db:
        assert (await db.get(User, "student")).is_active is False
    for query in ("page=0", "page_size=101"):
        assert (await client.get(PREFIX + "/users?" + query, headers=headers())).status_code == 422


async def test_document_safe_details_and_question_edit_roundtrip(console_app):
    async with console_app.sessions() as db:
        db.add(Document(id="doc", filename="资料.pdf", file_type="pdf", sha256="a" * 64,
                        local_path="C:/secret/path", metadata_={"content": "private text"}))
        await db.flush()
        db.add(DocumentChunk(id="chunk", doc_id="doc", chunk_index=0, text_content="private chunk"))
        await db.flush()
        db.add(QuestionBank(id="q", source_chunk_id="chunk", question_type="single_choice",
                            question_text="测试题", tags=["标签检索"], options=[{"label": "A", "text": "选项"}], correct_answer="A", analysis="解析"))
        await db.commit()
    client = console_app.client
    response = await client.get(PREFIX + "/documents/doc", headers=headers())
    assert response.status_code == 200
    assert "secret" not in response.text and "private" not in response.text and "metadata" not in response.text
    response = await client.patch(PREFIX + "/documents/doc", headers=headers(), json={"filename": " 新名称.pdf "})
    assert response.json()["filename"] == "新名称.pdf"
    assert (await client.get(PREFIX + "/documents?search=新名称", headers=headers())).json()["total"] == 1
    question = (await client.get(PREFIX + "/questions?search=测试", headers=headers())).json()["items"][0]
    assert question["answer"] == "A" and question["source_doc_id"] == "doc"
    assert (await client.get(PREFIX + "/questions", params={"search": "标签检索"}, headers=headers())).json()["total"] == 1
    patch = {"question_text": "修订题", "answer": "B", "analysis": "新解析", "difficulty": "hard"}
    response = await client.patch(PREFIX + "/questions/q", headers=headers(), json=patch)
    assert response.status_code == 200, response.text
    assert all(response.json()[key] == value for key, value in patch.items())
    assert (await client.patch(PREFIX + "/questions/q", headers=headers(), json={"answer": "  "})).status_code == 422
    assert (await client.patch(PREFIX + "/documents/doc", headers=headers(), json={"filename": " "})).status_code == 422
    assert (await client.patch(PREFIX + "/documents/missing", headers=headers(), json={"filename": "ok"})).status_code == 404


@pytest.mark.parametrize("path,payload", [
    ("/models", {"model": "", "daily_token_limit": 0}),
    ("/models", {"model": "m", "fallback_models": [" "]}),
    ("/keys", {"provider": " ", "api_key": "secret"}),
    ("/keys", {"provider": "openai", "api_key": " "}),
    ("/keys", {"provider": "x" * 51, "api_key": "secret"}),
])
async def test_operational_writes_validate_without_external_calls(console_app, path, payload):
    response = await console_app.client.post(PREFIX + path, headers=headers(), json=payload)
    assert response.status_code == 422, response.text


@pytest.fixture
def isolated_scheduler(monkeypatch, tmp_path):
    import app.core.crypto as crypto
    import app.core.api_scheduler as scheduler
    import app.core.admin_console_runtime as runtime
    from app.core.api_scheduler.client import UnifiedAPIClient
    client = UnifiedAPIClient()
    monkeypatch.setattr(scheduler, "api_client", client)
    monkeypatch.setattr(runtime, "api_client", client)
    monkeypatch.setattr(settings, "daily_token_limit", settings.daily_token_limit)
    monkeypatch.setattr(crypto, "_KEY_FILE", tmp_path / "secret.key")
    return client


async def test_keys_register_correct_provider_and_disable_delete_live_adapter(console_app, isolated_scheduler):
    client = console_app.client
    response = await client.post(PREFIX + "/keys", headers=headers(),
                                 json={"provider": "qwen", "api_key": "private-test-key", "key_alias": "全局"})
    assert response.status_code == 200, response.text
    key_id = response.json()["key_id"]
    adapter = isolated_scheduler.get_adapter("qwen/qwen-plus")
    assert adapter.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert adapter.model_name == "qwen-plus"
    result = await client.get(PREFIX + "/keys", headers=headers())
    assert result.status_code == 200 and "private-test-key" not in result.text and "key_encrypted" not in result.text
    assert (await client.put(PREFIX + f"/keys/{key_id}/toggle", headers=headers())).json()["is_active"] is False
    assert "qwen" not in isolated_scheduler._adapters
    await client.put(PREFIX + f"/keys/{key_id}/toggle", headers=headers())
    assert "qwen" in isolated_scheduler._adapters
    assert (await client.delete(PREFIX + f"/keys/{key_id}", headers=headers())).status_code == 200
    assert "qwen" not in isolated_scheduler._adapters
    assert (await client.get(PREFIX + "/keys", headers=headers())).json() == []


async def test_model_save_reports_persisted_scope_and_health_reads_actual_scheduler(console_app, isolated_scheduler):
    client = console_app.client
    payload = {"model": "qwen/qwen-plus", "fallback_models": ["deepseek-chat"], "daily_token_limit": 42}
    response = await client.post(PREFIX + "/models", headers=headers(), json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["scope"] == "default_requests"
    response = await client.get(PREFIX + "/models", headers=headers())
    assert all(response.json()[key] == value for key, value in payload.items())
    response = await client.get(PREFIX + "/health", headers=headers())
    assert response.status_code == 200, response.text
    assert response.json()["database"] == "ok"
    status = response.json()["api_scheduler"]
    assert status["status"] == "unconfigured" and status["configured_adapters"] == 0
    assert status["default_model"] == payload["model"]


async def test_logs_cache_and_quota_success_paths_remain_in_disposable_storage(console_app, monkeypatch):
    # Load only legacy handler file, avoiding the unrelated app.api eager package.
    source = Path(__file__).parents[1] / "app/api/admin.py"
    spec = importlib.util.spec_from_file_location("isolated_legacy_admin", source)
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    monkeypatch.setattr(console_app.module, "_legacy_admin", lambda: legacy)
    cache_module = __import__("app.core.api_scheduler.cache", fromlist=["async_session"])
    limiter_module = __import__("app.core.api_scheduler.rate_limiter", fromlist=["async_session"])
    monkeypatch.setattr(cache_module, "async_session", console_app.sessions)
    monkeypatch.setattr(limiter_module, "async_session", console_app.sessions)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with console_app.sessions() as db:
        db.add_all([
            APICallLog(id="old", task_type="chat", model_name="x", created_at=now - timedelta(minutes=1)),
            APICallLog(id="new", task_type="chat", model_name="y", created_at=now,
                       error_message="private error", content_summary="private summary"),
            APICache(cache_key="test", response_content="private response", model_used="x", tokens_output=12),
            APIQuota(user_id="admin", daily_limit=100, used_today=25, reset_at=now.date()),
        ])
        await db.commit()
    client = console_app.client
    response = await client.get(PREFIX + "/logs?limit=1", headers=headers())
    assert response.status_code == 200 and response.json()[0]["id"] == "new"
    assert "private" not in response.text
    assert (await client.get(PREFIX + "/logs?limit=501", headers=headers())).status_code == 422
    response = await client.get(PREFIX + "/cache/stats", headers=headers())
    assert response.json() == {"total_entries": 1, "total_tokens_saved": 12}
    response = await client.get(PREFIX + "/quota/status", headers=headers())
    assert response.json() == {"daily_limit": 100, "used_today": 25, "remaining": 75}
    assert (await client.delete(PREFIX + "/cache/clear", headers=headers())).status_code == 200
    assert (await client.get(PREFIX + "/cache/stats", headers=headers())).json()["total_entries"] == 0


async def test_key_mutation_validation_precedes_persistence(console_app, isolated_scheduler):
    async with console_app.sessions() as db:
        db.add_all([APIKey(id="legacy", provider="unsupported-legacy", key_encrypted="broken", is_active=False),
                    APIKey(id="corrupt", provider="qwen", key_encrypted="broken", is_active=False)])
        await db.commit()
    client = console_app.client
    response = await client.put(PREFIX + "/keys/legacy/toggle", headers=headers())
    assert response.status_code == 422, response.text
    assert (await client.delete(PREFIX + "/keys/legacy", headers=headers())).status_code == 200
    assert (await client.post(PREFIX + "/keys", headers=headers(), json={"provider": "qwen", "api_key": "valid-key"})).status_code == 200
    response = await client.put(PREFIX + "/keys/corrupt/toggle", headers=headers())
    assert response.status_code == 422, response.text
    assert "broken" not in response.text
    async with console_app.sessions() as db:
        assert (await db.get(APIKey, "corrupt")).is_active is False
        assert await db.get(APIKey, "legacy") is None
