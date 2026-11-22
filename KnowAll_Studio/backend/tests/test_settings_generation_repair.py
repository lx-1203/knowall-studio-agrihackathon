from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.dialects import mysql
from app.api import flashcards
from app.core.api_scheduler.client import UnifiedAPIClient
from tests.test_admin_console_runtime import storage


async def test_due_query_is_valid_for_mysql():
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    await flashcards.get_due_cards(db=db, current_user=SimpleNamespace(id='alice'))
    sql = str(db.execute.await_args.args[0].compile(dialect=mysql.dialect()))
    assert 'NULLS FIRST' not in sql
    assert 'ORDER BY' in sql


async def test_live_models_use_effective_account_provider_and_keep_new_ids(monkeypatch):
    from app.core.model_catalog import discover_models
    client = UnifiedAPIClient()
    client.configure_adapter('openai', 'global', base_url='https://global.test/v1')
    client.configure_adapter('openai_alice', 'alice-secret', base_url='https://alice.test/v1', owner_user_id='alice')
    client.configure_adapter('deepseek_bob', 'bob-secret', owner_user_id='bob')
    seen = []
    def provider(request):
        seen.append(request)
        assert str(request.url) == 'https://alice.test/v1/models'
        assert request.headers['authorization'] == 'Bearer alice-secret'
        return httpx.Response(200, json={'data': [{'id': 'future-model'}, {'id': 'future-model', 'name': 'Future Model Updated'}, {'id': 'new-release'}]})
    transport = httpx.MockTransport(provider)
    async with httpx.AsyncClient(transport=transport) as http:
        result = await discover_models(client, 'alice', http=http)
    assert {m['value'] for m in result['models']} == {'openai/future-model', 'openai/new-release'}
    assert len(seen) == 1 and result['source'] == 'provider' and result['fetched_at']
    assert not result['errors']
    assert 'secret' not in str(result)
    future = next(m for m in result['models'] if m['model_id'] == 'future-model')
    assert future['display_name'] == 'Future Model Updated'
    assert 'Future Model Updated (future-model)' in future['label']
    assert {m['value'] for m in client.get_agent_models('alice')} == {'openai/future-model', 'openai/new-release'}
    client.configure_adapter('openai_alice', 'rotated', base_url='https://alice.test/v1', owner_user_id='alice')
    assert 'openai/future-model' not in {m['value'] for m in client.get_agent_models('alice')}


async def test_live_catalog_failure_is_not_reported_as_a_current_list():
    from app.core.model_catalog import discover_models
    client = UnifiedAPIClient()
    client.configure_adapter('deepseek', 'never-expose-this')
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(401, text='never-expose-this'))) as http:
        result = await discover_models(client, 'alice', http=http)
    assert result['models'] == [] and result['errors']
    assert 'never-expose-this' not in str(result)


async def test_settings_key_save_registers_correct_url_and_aliases(storage):
    from app.api import admin
    db, client = storage
    await admin.add_api_key(admin.AddAPIKeyRequest(provider='qwen', api_key='synthetic-key'), db=db)
    assert client.get_adapter('qwen-plus').base_url == 'https://dashscope.aliyuncs.com/compatible-mode/v1'


async def test_settings_model_save_applies_without_restart(storage, monkeypatch):
    from app.api import admin
    from app.core.admin_console_runtime import settings
    monkeypatch.setattr(settings, 'daily_token_limit', settings.daily_token_limit)
    db, client = storage
    await admin.configure_model(admin.ModelConfigRequest(model='openai/new-release'), db=db)
    assert client.get_default_configuration().model == 'openai/new-release'
