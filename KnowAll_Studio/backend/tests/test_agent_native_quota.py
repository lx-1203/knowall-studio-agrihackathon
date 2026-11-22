"""Real quota SQL; only provider results are simulated, never paid."""
import asyncio
import copy
from types import SimpleNamespace

import pytest
from app.core.api_scheduler.client import GenerationConfig, QuotaExceededError
from app.core.api_scheduler.adapters.base import InvalidResponseError
from app.models import APIQuota, APICallLog, APICache,APIKey
from tests.test_generation_cache_isolation_flow import cache_flow, rows
from tests.test_study_notification_flow import flow


def response():
    return SimpleNamespace(content='回答 [S1]', raw_content='{}', model='explicit-simulated',
        tool_calls=[], tokens_input=7, tokens_output=11, finish_reason='stop')


def configure(f, behavior=None):
    calls=[]
    async def native(messages, tools, config):
        calls.append(copy.deepcopy(messages))
        if behavior:
            return await behavior()
        return response()
    f.adapters['primary_alice'].chat_completion_with_tools=native
    return calls


async def turn(f, **kwargs):
    return await f.api.agent_turn(messages=[{'role':'user','content':'细胞'}], tools=[],
        config=GenerationConfig(model='primary',max_tokens=32),user_id='alice',**kwargs)


async def test_every_native_turn_accounts_usage_and_never_uses_generation_cache(cache_flow):
    f=cache_flow; calls=configure(f)
    await turn(f); await turn(f)
    assert len(calls)==2
    assert not await rows(f,APICache)
    quotas=await rows(f,APIQuota); logs=await rows(f,APICallLog)
    assert quotas[0].user_id=='alice' and quotas[0].used_today==36
    assert len(logs)==2 and all(log.success and not log.from_cache for log in logs)


async def test_invalid_native_response_accounts_billed_usage_without_retry(cache_flow):
    async def bad(): raise InvalidResponseError('truncated tool arguments',7,11)
    f=cache_flow; calls=configure(f,bad)
    with pytest.raises(InvalidResponseError): await turn(f)
    assert len(calls)==1 and (await rows(f,APIQuota))[0].used_today==18
    assert len(await rows(f,APICallLog))==1 and not (await rows(f,APICallLog))[0].success
    assert not await rows(f,APICache)


async def test_quota_denial_has_no_native_model_call(cache_flow,monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings,'daily_token_limit',1)
    calls=configure(cache_flow)
    with pytest.raises(QuotaExceededError): await turn(cache_flow)
    assert calls==[] and not await rows(cache_flow,APICallLog)


async def test_foreign_registered_adapter_cannot_be_native_fallback(cache_flow):
    f=cache_flow; f.api._adapters={'primary_bob':f.adapters['primary_bob']}
    with pytest.raises(ValueError,match='configured'): await turn(f)
    assert not await rows(f,APIQuota) and not f.adapters['primary_bob'].calls


async def test_explicit_foreign_registration_name_cannot_bypass_native_scope(cache_flow):
    f=cache_flow
    f.api._adapter_owners={'primary_bob':'bob'}
    with pytest.raises(ValueError,match='configured'):
        f.api.get_agent_adapter('primary_bob','alice')


def test_adapter_registration_retains_server_owner_metadata():
    from app.core.api_scheduler.client import UnifiedAPIClient
    client=UnifiedAPIClient()
    client.configure_adapter('openai_bob','explicit-test-key',owner_user_id='bob')
    assert client._adapter_owners['openai_bob']=='bob'
    assert client.get_agent_adapter('openai','bob') is client._adapters['openai_bob']
    with pytest.raises(ValueError):client.get_agent_adapter('openai_bob','alice')


async def test_actual_key_loader_marks_owner_and_refresh_removes_disabled_key(cache_flow,monkeypatch):
    from app.core import auth,crypto
    f=cache_flow
    monkeypatch.setattr(crypto,'decrypt_api_key',lambda value:'explicit-only-test-key')
    async with f.factory() as db:
        db.add(APIKey(id='native-key',user_id='alice',provider='openai',key_alias='test',key_encrypted='simulation',is_active=True))
        await db.commit()
        await auth.load_user_api_keys('alice',db)
        assert f.api._adapter_owners['openai_alice']=='alice'
        with pytest.raises(ValueError):f.api.get_agent_adapter('openai_alice','bob')
        key=await db.get(APIKey,'native-key');key.is_active=False;await db.commit()
        await auth.load_user_api_keys('alice',db)
    assert 'openai_alice' not in f.api._adapters
    assert 'openai_alice' not in f.api._adapter_owners


async def test_concurrent_native_turns_recheck_quota_after_earlier_usage(cache_flow,monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings,'daily_token_limit',40)
    f=cache_flow
    f.adapters['primary_alice'].count_tokens=lambda text:0
    async def slow():
        await asyncio.sleep(.05)
        return response()
    calls=configure(f,slow)
    results=await asyncio.gather(turn(f),turn(f),return_exceptions=True)
    assert len(calls)==1
    assert sum(isinstance(result,QuotaExceededError) for result in results)==1
    assert (await rows(f,APIQuota))[0].used_today==18


async def test_key_refresh_keeps_previous_private_adapter_until_decryption_finishes(cache_flow,monkeypatch):
    from app.core import auth,crypto
    f=cache_flow
    monkeypatch.setattr(crypto,'decrypt_api_key',lambda value:'explicit-old-test-key')
    async with f.factory() as db:
        db.add(APIKey(id='refresh-key',user_id='alice',provider='openai',key_alias='test',key_encrypted='simulation',is_active=True))
        await db.commit();await auth.load_user_api_keys('alice',db)
        previous=f.api.get_agent_adapter('openai','alice')
        started=asyncio.Event();release=asyncio.Event()
        async def decrypt(*a):started.set();await release.wait();return 'explicit-new-test-key'
        monkeypatch.setattr(auth,'run_in_threadpool',decrypt)
        job=asyncio.create_task(auth.load_user_api_keys('alice',db))
        try:
            await asyncio.wait_for(started.wait(),2)
            assert f.api.get_agent_adapter('openai','alice') is previous
        finally:
            release.set();await job
        assert f.api.get_agent_adapter('openai','alice') is not previous


@pytest.mark.parametrize('provider,alias',[('deepseek','deepseek-chat'),('openai','gpt-4o-mini'),('anthropic','claude-sonnet-4-6')])
def test_known_model_alias_uses_its_configured_provider_without_cross_account_fallback(provider,alias):
    from app.core.api_scheduler.client import UnifiedAPIClient
    client=UnifiedAPIClient()
    client.configure_adapter(provider,'explicit-global-test-key',model_name='registered-default')
    adapter=client.get_agent_adapter(alias,'alice')
    assert adapter.model_name==alias
    assert client._adapters[provider].model_name=='registered-default'


def test_known_alias_can_use_only_current_account_provider_registration():
    from app.core.api_scheduler.client import UnifiedAPIClient
    client=UnifiedAPIClient()
    client.configure_adapter('openai_alice','explicit-private-test-key',owner_user_id='alice')
    assert client.get_agent_adapter('gpt-4o','alice').api_key=='explicit-private-test-key'
    with pytest.raises(ValueError):client.get_agent_adapter('gpt-4o','bob')


async def test_cancelled_provider_releases_concurrency_and_never_retries(cache_flow,monkeypatch):
    import app.core.api_scheduler.client as module
    arrived=asyncio.Event()
    async def blocked(): arrived.set(); await asyncio.Event().wait()
    calls=configure(cache_flow,blocked)
    original=module.rate_limiter._semaphore._value
    job=asyncio.create_task(turn(cache_flow))
    try: await asyncio.wait_for(arrived.wait(),2)
    finally:
        if not arrived.is_set():
            job.cancel()
            await asyncio.gather(job,return_exceptions=True)
    job.cancel()
    with pytest.raises(asyncio.CancelledError): await job
    assert len(calls)==1 and module.rate_limiter._semaphore._value==original
    assert not await rows(cache_flow,APICache)
