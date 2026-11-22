"""Console configuration changes must affect real scheduler routing without model calls."""
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.models import APIKey, User
from app.models.system_config import SystemConfig
from app.core.api_scheduler.client import UnifiedAPIClient, GenerationConfig, TaskType
from app.core import admin_console_runtime as runtime
from app.core.crypto import encrypt_api_key


@pytest.fixture
async def storage(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    async with engine.begin() as connection:
        for model in (User, APIKey, SystemConfig):
            await connection.run_sync(model.__table__.create)
    client = UnifiedAPIClient()
    monkeypatch.setattr(runtime, "api_client", client)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        yield db, client
    await engine.dispose()


def test_default_configuration_is_copied_and_explicit_config_unchanged():
    client = UnifiedAPIClient()
    client.set_default_configuration("new-model", ["backup-model"])
    first = client.get_default_configuration()
    first.fallback_models.append("mutated")
    assert client.get_default_configuration().model == "new-model"
    assert client.get_default_configuration().fallback_models == ["backup-model"]
    explicit = GenerationConfig(model="tool-model")
    assert explicit.model == "tool-model"


async def test_generate_and_chat_use_defaults_but_preserve_explicit_models(monkeypatch):
    from types import SimpleNamespace
    client = UnifiedAPIClient()
    client.set_default_configuration("saved-model", ["backup-model"])

    class AtAdapter(BaseException):
        pass

    def stop_before_any_io(model_ref, user_id=None):
        raise AtAdapter(model_ref)

    monkeypatch.setattr(client, "get_adapter", stop_before_any_io)
    for config, expected in ((None, "saved-model"), (GenerationConfig(model="explicit-model"), "explicit-model")):
        with pytest.raises(AtAdapter, match=expected):
            await client.generate(TaskType.CHAT, [], "test", "synthetic", config=config)
    seen = []

    async def capture(**kwargs):
        seen.append(kwargs["config"].model)
        return SimpleNamespace(content="local-only")

    monkeypatch.setattr(client, "generate", capture)
    await client.chat("synthetic")
    await client.chat("synthetic", model="explicit-chat")
    assert seen == ["saved-model", "explicit-chat"]


async def test_saved_global_key_registers_correct_provider_url_and_disable_removes_it(storage):
    db, client = storage
    key = APIKey(provider="qwen", key_encrypted=encrypt_api_key("synthetic-secret"), is_active=True)
    db.add(key); await db.commit()
    await runtime.sync_provider_keys(db, "qwen")
    assert client.get_adapter("qwen-plus").base_url.rstrip("/") == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert "qwen-plus" in client._adapters
    key.is_active = False; await db.commit()
    await runtime.sync_provider_keys(db, "qwen")
    assert "qwen-plus" not in client._adapters


async def test_owner_sync_preserves_other_accounts_and_restores_global_config(storage, monkeypatch):
    import json
    db, client = storage
    monkeypatch.setattr(runtime.settings, "daily_token_limit", 123)
    db.add(User(id="owner", username="owner", email="owner@test.invalid", password_hash="unused"))
    db.add_all([
        APIKey(provider="openai", user_id="owner", key_encrypted=encrypt_api_key("owned-secret")),
        APIKey(provider="deepseek", key_encrypted=encrypt_api_key("global-secret")),
        SystemConfig(key="model_config", value=json.dumps({"model": "deepseek-chat", "fallback_models": ["openai"], "daily_token_limit": 456})),
    ])
    await db.commit()
    await runtime.sync_provider_keys(db, "openai", "owner")
    await runtime.restore_console_runtime(db)
    assert client._adapter_owners["openai_owner"] == "owner"
    assert client._adapter_owners["deepseek-chat"] is None
    assert client.get_default_configuration().model == "deepseek-chat"
    assert runtime.settings.daily_token_limit == 456
    assert "api_key" not in json.dumps(runtime.scheduler_status())


@pytest.mark.parametrize("bad_config", ['{"model":"","daily_token_limit":0}', '[]', '"invalid"', 'not-json'])
async def test_invalid_saved_model_does_not_prevent_restoring_valid_keys(storage, monkeypatch, bad_config):
    db, client = storage
    monkeypatch.setattr(runtime.settings, "default_model", "environment-model")
    db.add_all([
        SystemConfig(key="model_config", value=bad_config),
        APIKey(provider="deepseek", key_encrypted=encrypt_api_key("synthetic-secret")),
    ])
    await db.commit()
    await runtime.restore_console_runtime(db)
    assert client.get_default_configuration().model == "environment-model"
    assert "deepseek-chat" in client._adapters
