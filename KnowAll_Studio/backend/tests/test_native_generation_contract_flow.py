"""Native scheduler, disposable SQL/cache/quota; model adapters explicitly simulated."""
import asyncio
import copy
import pytest
from app.core.api_scheduler.client import GenerationConfig, RequestContext, AllModelsFailedError
from app.models import APICache, APICallLog, APIQuota
from tests.test_generation_cache_isolation_flow import flow, cache_flow, rows


@pytest.mark.parametrize('mode',['absent','none','configured','configured_none'])
async def test_chat_preserves_default_or_configured_limit(cache_flow,mode):
    kwargs={}
    if mode.startswith('configured'):
        kwargs['config']=GenerationConfig(model='primary',max_tokens=777,max_retries=0)
    else:
        kwargs['model']='primary'
    if mode.endswith('none'):kwargs['max_tokens']=None
    result=await cache_flow.api.chat('明确模拟默认参数',**kwargs)
    assert result
    assert cache_flow.adapters['primary'].calls[0]['config']['max_tokens']==(777 if mode.startswith('configured') else 4096)


@pytest.mark.parametrize('limit',[256,4096,8192])
async def test_chat_limit_override_reaches_adapter_without_changing_config(cache_flow,limit):
    config=GenerationConfig(model='primary',max_tokens=777,temperature=.2,top_p=.8,max_retries=0,timeout=23)
    before=copy.deepcopy(config)
    await cache_flow.api.chat('明确模拟教材和研究参数',config=config,max_tokens=limit,task_type='book_gen')
    actual=cache_flow.adapters['primary'].calls[0]['config']
    assert actual['max_tokens']==limit and actual['temperature']==.2 and actual['top_p']==.8 and actual['timeout']==23
    assert config==before


async def test_chat_model_override_does_not_redirect_next_use_of_shared_config(cache_flow):
    config=GenerationConfig(model='primary',max_retries=0)
    before=copy.deepcopy(config)
    alternate=await cache_flow.api.chat('同一问题',model='alternate',config=config)
    primary=await cache_flow.api.chat('同一问题',config=config)
    assert config==before
    assert 'alternate' in alternate and 'primary' in primary
    assert len(cache_flow.adapters['primary'].calls)==len(cache_flow.adapters['alternate'].calls)==1


async def test_chat_overridden_limit_has_distinct_paid_cache_and_identical_repeat_is_free(cache_flow):
    config=GenerationConfig(model='primary',max_retries=0,max_tokens=777)
    args=dict(prompt='同一参数缓存问题',config=config,context=RequestContext(user_id='alice'))
    for limit in (256,8192,256,8192):await cache_flow.api.chat(**args,max_tokens=limit)
    calls=cache_flow.adapters['primary_alice'].calls
    assert [c['config']['max_tokens'] for c in calls]==[256,8192]
    assert len(await rows(cache_flow,APICache))==len(await rows(cache_flow,APICallLog))==2
    quota=(await rows(cache_flow,APIQuota))[0]
    assert quota.user_id=='alice' and quota.used_today==quota.total_used==36
    assert config.max_tokens==777


async def test_concurrent_chat_overrides_keep_shared_config_and_separate_models(cache_flow):
    config=GenerationConfig(model='primary',max_tokens=777,max_retries=0)
    before=copy.deepcopy(config)
    await asyncio.gather(cache_flow.api.chat('并发A',config=config,model='primary',max_tokens=256),
                         cache_flow.api.chat('并发B',config=config,model='alternate',max_tokens=8192))
    assert config==before
    assert cache_flow.adapters['primary'].calls[0]['config']['max_tokens']==256
    assert cache_flow.adapters['alternate'].calls[0]['config']['max_tokens']==8192


async def test_chat_override_keeps_response_validator_and_failed_paid_usage(cache_flow):
    config=GenerationConfig(model='primary',max_retries=0,response_validator=lambda text:False)
    with pytest.raises(AllModelsFailedError):
        await cache_flow.api.chat('拒绝明确模拟坏结果',config=config,max_tokens=256)
    assert not await rows(cache_flow,APICache)
    logs=await rows(cache_flow,APICallLog)
    assert len(logs)==1 and not logs[0].success
    assert (await rows(cache_flow,APIQuota))[0].used_today==18
    assert config.response_validator is not None and config.max_tokens==4096


@pytest.mark.parametrize('explicit_messages',[False,True])
async def test_chat_override_preserves_message_and_system_contract(cache_flow,explicit_messages):
    messages=[{'role':'system','content':'明确模拟教师'}, {'role':'user','content':'明确模拟问题'}]
    kwargs={'messages':copy.deepcopy(messages)} if explicit_messages else {'system':messages[0]['content']}
    await cache_flow.api.chat(messages[1]['content'],model='primary',max_tokens=256,**kwargs)
    assert cache_flow.adapters['primary'].calls[0]['messages']==messages


async def test_chat_override_still_selects_user_scoped_adapter(cache_flow):
    await cache_flow.api.chat('明确模拟账号选择',model='primary',max_tokens=256,context=RequestContext(user_id='alice'))
    assert not cache_flow.adapters['primary'].calls
    assert cache_flow.adapters['primary_alice'].calls[0]['config']['max_tokens']==256
    assert (await rows(cache_flow,APIQuota))[0].user_id=='alice'
