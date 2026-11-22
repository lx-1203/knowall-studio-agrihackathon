"""Real config/client/SQLite; only provider completion explicitly simulated."""
import asyncio,json,hashlib
from types import SimpleNamespace
import pytest
from app.models import APICache,APIQuota,APICallLog
from app.core.api_scheduler.client import UnifiedAPIClient,GenerationConfig,RequestContext,TaskType,AllModelsFailedError
from app.core.api_scheduler.adapters.base import AdapterResponse
from app.core.api_scheduler.adapters.openai import OpenAICompatAdapter
from app.core.api_scheduler.adapters.anthropic import AnthropicAdapter
from tests.test_generation_cache_isolation_flow import cache_flow,rows
from tests.test_study_notification_flow import flow


def args(user='alice',fallback=False):
    return dict(task_type=TaskType.CHAT,messages=[{'role':'user','content':'明确模拟同一输入'}],
        prompt_template_id='simulated.config',generation_content='明确模拟同一输入',
        config=GenerationConfig(model='openai',fallback_models=['anthropic'] if fallback else [],max_retries=0),context=RequestContext(user_id=user))


def register(api,user='alice',provider='openai',key='explicit-simulated-key-A',url='http://provider-a.invalid/v1',model='simulated-model-A'):
    api.configure_adapter(provider+'_'+user,key,base_url=url,model_name=model)


@pytest.fixture
async def configured(cache_flow,monkeypatch):
    calls=[]
    async def simulated(adapter,messages,config):
        identity=[type(adapter).__name__,adapter.api_key,adapter.base_url.rstrip('/'),adapter.model_name]
        marker=hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:16]
        calls.append({'marker':marker,'model':adapter.model_name,'protocol':type(adapter).__name__})
        if adapter.api_key.endswith('-fail'):raise RuntimeError('explicit simulated provider failure')
        content=json.dumps({'answer':'明确模拟-'+marker},ensure_ascii=False)
        return AdapterResponse(content,content,adapter.model_name,7,11)
    monkeypatch.setattr(OpenAICompatAdapter,'chat_completion',simulated)
    monkeypatch.setattr(AnthropicAdapter,'chat_completion',simulated)
    for user in ('alice','bob'):
        register(cache_flow.api,user)
        register(cache_flow.api,user,'anthropic',url='http://fallback-a.invalid/v1')
    yield SimpleNamespace(**vars(cache_flow),calls=calls)


CHANGES=[{'key':'explicit-simulated-key-B'},{'url':'http://provider-b.invalid/v1'},
         {'model':'simulated-model-B'}, {'url':'http://provider-a.invalid/anthropic','provider':'deepseek'}]


@pytest.mark.parametrize('field',['key','url','model','protocol'])
async def test_resolved_provider_config_change_invalidates_owned_cache(configured,field):
    if field=='protocol':
        register(configured.api,provider='deepseek',url='http://provider-a.invalid/v1')
        request=args();request['config'].model='deepseek'
    else:request=args()
    first=await configured.api.generate(**request)
    change=CHANGES[['key','url','model','protocol'].index(field)]
    register(configured.api,**change)
    second=await configured.api.generate(**request)
    assert not second.from_cache and second.content!=first.content
    assert len(await rows(configured,APICache))==2
    assert (await rows(configured,APIQuota))[0].used_today==36


@pytest.mark.parametrize('field',['key','url','model'])
async def test_fallback_config_change_invalidates_request_identity(configured,field):
    register(configured.api,key='explicit-simulated-fail')
    first=await configured.api.generate(**args(fallback=True))
    register(configured.api,provider='anthropic',**{'url':'http://fallback-a.invalid/v1',**CHANGES[['key','url','model'].index(field)]})
    second=await configured.api.generate(**args(fallback=True))
    assert not second.from_cache and second.content!=first.content
    assert len(await rows(configured,APICache))==2


async def test_unused_fallback_config_change_cannot_reuse_old_candidate_set(configured):
    first=await configured.api.generate(**args(fallback=True))
    register(configured.api,provider='anthropic',key='explicit-simulated-key-B',url='http://fallback-a.invalid/v1')
    second=await configured.api.generate(**args(fallback=True))
    assert not second.from_cache and second.content==first.content
    assert len(await rows(configured,APICache))==2


@pytest.mark.parametrize('reload',['registration','new-client','trailing-slash'])
async def test_equivalent_effective_configuration_reuses_persistent_cache(configured,reload):
    first=await configured.api.generate(**args())
    api=UnifiedAPIClient() if reload=='new-client' else configured.api
    register(api,url='http://provider-a.invalid/v1/' if reload=='trailing-slash' else 'http://provider-a.invalid/v1')
    second=await api.generate(**args())
    assert second.from_cache and second.content==first.content
    assert len(configured.calls)==1 and len(await rows(configured,APICallLog))==1


async def test_other_account_configuration_does_not_invalidate_current_account(configured):
    first=await configured.api.generate(**args())
    register(configured.api,user='bob',key='explicit-simulated-key-B')
    second=await configured.api.generate(**args())
    bob=await configured.api.generate(**args('bob'))
    assert second.from_cache and second.content==first.content
    assert not bob.from_cache and bob.content!=first.content
    assert {q.user_id:q.used_today for q in await rows(configured,APIQuota)}=={'alice':18,'bob':18}


async def test_config_restored_uses_its_own_previous_entry_without_plaintext_key(configured):
    first=await configured.api.generate(**args())
    register(configured.api,key='explicit-simulated-key-B')
    assert not (await configured.api.generate(**args())).from_cache
    register(configured.api)
    third=await configured.api.generate(**args())
    assert third.from_cache and third.content==first.content


async def test_unscoped_version_three_configuration_cache_is_not_reused(configured):
    request=args();config=request['config'];context=request['context']
    old={'version':3,'messages':request['messages'],'content':request['generation_content'],
        'template':request['prompt_template_id'],'task':request['task_type'].value,'model':config.model,
        'fallback':config.fallback_models,'sampling':[config.temperature,config.top_p,config.max_tokens],
        'scope':[context.user_id,context.project_id,context.api_key_level]}
    key=hashlib.sha256(json.dumps(old,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    async with configured.factory() as db:
        db.add(APICache(cache_key=key,response_content='old-provider-config',model_used='old-model'));await db.commit()
    result=await configured.api.generate(**request)
    assert not result.from_cache and result.content!='old-provider-config'
    assert len(await rows(configured,APICache))==2
    values=await rows(configured,APICache)
    assert len(values)==2 and all(len(c.cache_key)==64 for c in values)
    assert 'explicit-simulated-key' not in json.dumps([vars(c) for c in values],default=str)


async def test_client_without_configuration_cannot_reuse_configured_cache(configured,monkeypatch):
    await configured.api.generate(**args())
    for env in ('OPENAI_API_KEY','DEEPSEEK_API_KEY'):monkeypatch.delenv(env,raising=False)
    api=UnifiedAPIClient()
    with pytest.raises(AllModelsFailedError):await api.generate(**args())
    assert len(await rows(configured,APICache))==1


async def test_explicit_model_override_is_the_effective_model_for_cache(configured):
    request=args();request['config'].model='openai/explicit-fixed-model'
    first=await configured.api.generate(**request)
    register(configured.api,model='unused-default-model-B')
    second=await configured.api.generate(**request)
    assert second.from_cache and second.content==first.content


@pytest.mark.parametrize('fallback',[False,True])
async def test_inflight_reconfiguration_keeps_adapter_and_cache_identity_together(configured,monkeypatch,fallback):
    from app.core.api_scheduler.client import cache_manager
    if fallback:register(configured.api,key='explicit-simulated-fail')
    entered=asyncio.Event();resume=asyncio.Event();get=cache_manager.get_entry
    async def blocked(key):entered.set();await resume.wait();return await get(key)
    monkeypatch.setattr(cache_manager,'get_entry',blocked)
    task=asyncio.create_task(configured.api.generate(**args(fallback=fallback)))
    try:
        await asyncio.wait_for(entered.wait(),3)
        register(configured.api,provider='anthropic' if fallback else 'openai',key='explicit-simulated-key-B')
        resume.set();first=await task
    finally:
        resume.set()
        if not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
        monkeypatch.setattr(cache_manager,'get_entry',get)
    second=await configured.api.generate(**args(fallback=fallback))
    assert not second.from_cache and second.content!=first.content
    register(configured.api,provider='anthropic' if fallback else 'openai',
             key='explicit-simulated-key-A',url='http://fallback-a.invalid/v1' if fallback else 'http://provider-a.invalid/v1')
    third=await configured.api.generate(**args(fallback=fallback))
    assert third.from_cache and third.content==first.content
