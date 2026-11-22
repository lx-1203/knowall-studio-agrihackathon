"""Real disposable cache/quota/log SQL; external adapter explicitly simulated."""
import asyncio,copy,importlib
from types import SimpleNamespace
import pytest
from sqlalchemy import select,text
from datetime import date
from app.models import APIQuota,APICallLog,APICache
from app.core.api_scheduler.client import UnifiedAPIClient,GenerationConfig,RequestContext,TaskType,AllModelsFailedError,QuotaExceededError
from app.core.api_scheduler.adapters.base import BaseModelAdapter,AdapterResponse
from tests.test_cache_commit_race_flow import store

@pytest.fixture
async def controlled(store,monkeypatch):
    rate=importlib.import_module('app.core.api_scheduler.rate_limiter');client=importlib.import_module('app.core.api_scheduler.client')
    monkeypatch.setattr(rate,'async_session',store.factory);monkeypatch.setattr(client,'rate_limiter',rate.RateLimiter())
    state=SimpleNamespace(calls=[],entered=asyncio.Event(),release=asyncio.Event(),fail_first=False,contents=None,jobs=[])
    class Simulated(BaseModelAdapter):
        async def chat_completion(self,messages,config):
            index=len(state.calls);state.calls.append({'model':self.model_name,'messages':copy.deepcopy(messages),'config':vars(config).copy()})
            state.entered.set();await state.release.wait()
            if state.fail_first and index==0:raise RuntimeError('explicit simulated first failure')
            content=state.contents[index] if state.contents else '明确模拟-'+self.model_name
            return AdapterResponse(content,content,self.model_name,7,11)
        def count_tokens(self,text):return len(text)//4
    api=UnifiedAPIClient()
    for name in ('primary','primary_alice','primary_bob','alternate'):
        adapter=Simulated('explicit-simulated-key-'+name);adapter.model_name=name;api._adapters[name]=adapter
    def create(**changes):
        args=dict(task_type=TaskType.CHAT,messages=[{'role':'user','content':'同输入模拟'}],prompt_template_id='singleflight',
            generation_content='同输入模拟',config=GenerationConfig(model='primary',max_retries=0),context=RequestContext(user_id='alice'))
        args.update(changes);job=asyncio.create_task(api.generate(**args));state.jobs.append(job);return job
    try:yield SimpleNamespace(**vars(store),api=api,state=state,create=create,rate=client.rate_limiter)
    finally:
        state.release.set()
        for job in state.jobs:
            if not job.done():job.cancel()
        await asyncio.gather(*state.jobs,return_exceptions=True)

async def sql(flow,model):
    async with flow.factory() as db:return (await db.scalars(select(model))).all()

async def settle_before_release(flow):
    await asyncio.wait_for(flow.state.entered.wait(),3)
    # Deliberate 30ms simulated provider latency lets all started requests reach
    # their cache/queue boundary; this is scheduling coverage, not work padding.
    await asyncio.sleep(.03);flow.state.release.set()

@pytest.mark.parametrize('count',[2,8])
async def test_same_owned_request_in_flight_uses_one_paid_response(controlled,count):
    jobs=[controlled.create() for _ in range(count)];await settle_before_release(controlled)
    results=await asyncio.gather(*jobs)
    assert len(controlled.state.calls)==1
    assert sum(not r.from_cache for r in results)==1 and {r.content for r in results}=={'明确模拟-primary_alice'}
    assert (await sql(controlled,APIQuota))[0].used_today==18
    assert len(await sql(controlled,APICallLog))==len(await sql(controlled,APICache))==1
    assert not getattr(controlled.api,'_generation_locks',{})

@pytest.mark.parametrize('different',['user','project','permission','model','message','template','sampling'])
async def test_different_actual_request_can_run_while_first_provider_is_held(controlled,different):
    first=controlled.create();await asyncio.wait_for(controlled.state.entered.wait(),3)
    changes={}
    if different=='user':changes['context']=RequestContext(user_id='bob')
    elif different=='project':changes['context']=RequestContext(user_id='alice',project_id='another')
    elif different=='permission':changes['context']=RequestContext(user_id='alice',api_key_level='teacher')
    elif different=='model':changes['config']=GenerationConfig(model='alternate',max_retries=0)
    elif different=='message':changes['messages']=[{'role':'user','content':'another input'}]
    elif different=='template':changes['prompt_template_id']='another'
    elif different=='sampling':changes['config']=GenerationConfig(model='primary',max_tokens=777,max_retries=0)
    second=controlled.create(**changes)
    async with asyncio.timeout(3):
        while len(controlled.state.calls)<2:await asyncio.sleep(.001)
    controlled.state.release.set();results=await asyncio.gather(first,second)
    assert all(not r.from_cache for r in results) and len(await sql(controlled,APICache))==2
    assert sum(q.used_today for q in await sql(controlled,APIQuota))==36

async def test_cancel_waiter_does_not_cancel_or_duplicate_leader(controlled):
    first=controlled.create();await asyncio.wait_for(controlled.state.entered.wait(),3)
    waiter=controlled.create();await asyncio.sleep(.03);waiter.cancel()
    with pytest.raises(asyncio.CancelledError):await waiter
    assert not first.done() and len(controlled.state.calls)==1
    controlled.state.release.set();assert not (await first).from_cache
    assert (await controlled.create()).from_cache and len(controlled.state.calls)==1
    assert (await sql(controlled,APIQuota))[0].used_today==18 and not getattr(controlled.api,'_generation_locks',{})

async def test_cancel_leader_releases_waiter_and_later_cache(controlled):
    leader=controlled.create();await asyncio.wait_for(controlled.state.entered.wait(),3)
    waiter=controlled.create();await asyncio.sleep(.03);leader.cancel()
    with pytest.raises(asyncio.CancelledError):await leader
    controlled.state.release.set();assert not (await asyncio.wait_for(waiter,3)).from_cache
    assert (await controlled.create()).from_cache and len(controlled.state.calls)==2
    assert (await sql(controlled,APIQuota))[0].used_today==18 and len(await sql(controlled,APICallLog))==1
    assert not getattr(controlled.api,'_generation_locks',{})

async def test_failed_leader_is_not_shared_and_waiter_can_recover(controlled):
    controlled.state.fail_first=True
    leader=controlled.create();await asyncio.wait_for(controlled.state.entered.wait(),3)
    waiter=controlled.create();await settle_before_release(controlled)
    results=await asyncio.gather(leader,waiter,return_exceptions=True)
    assert isinstance(results[0],AllModelsFailedError) and not isinstance(results[1],BaseException)
    assert not results[1].from_cache and (await controlled.create()).from_cache
    assert sorted(r.success for r in await sql(controlled,APICallLog))==[False,True]
    assert (await sql(controlled,APIQuota))[0].used_today==18 and not getattr(controlled.api,'_generation_locks',{})

async def test_individual_validator_cannot_accept_leaders_incompatible_cache(controlled):
    controlled.state.contents=['first','second']
    first=controlled.create(config=GenerationConfig(model='primary',max_retries=0,response_validator=lambda s:s=='first'))
    second=controlled.create(config=GenerationConfig(model='primary',max_retries=0,response_validator=lambda s:s=='second'))
    await settle_before_release(controlled);results=await asyncio.gather(first,second)
    assert [r.content for r in results]==['first','second'] and all(not r.from_cache for r in results)
    assert len(controlled.state.calls)==2 and (await sql(controlled,APIQuota))[0].used_today==36
    assert not getattr(controlled.api,'_generation_locks',{})

async def test_provider_key_rotation_while_leader_held_uses_distinct_snapshot(controlled):
    leader=controlled.create();await asyncio.wait_for(controlled.state.entered.wait(),3)
    replacement=copy.copy(controlled.api._adapters['primary_alice']);replacement.api_key='explicit-simulated-rotated'
    controlled.api._adapters['primary_alice']=replacement
    second=controlled.create()
    async with asyncio.timeout(3):
        while len(controlled.state.calls)<2:await asyncio.sleep(.001)
    controlled.state.release.set();await asyncio.gather(leader,second)
    assert len(await sql(controlled,APICache))==2 and not getattr(controlled.api,'_generation_locks',{})

@pytest.mark.parametrize('field',['messages','sampling','context','fallback'])
async def test_input_mutation_while_provider_held_cannot_change_snapshot(controlled,field):
    messages=[{'role':'user','content':'原始输入'}];config=GenerationConfig(model='primary',max_retries=0)
    context=RequestContext(user_id='alice')
    job=controlled.create(messages=messages,config=config,context=context)
    await asyncio.wait_for(controlled.state.entered.wait(),3)
    # Provider records arguments at entry; usage/logs and queued requests must
    # remain tied to the original ownership/config even if caller changes them.
    if field=='messages':messages[0]['content']='后来修改'
    elif field=='sampling':config.max_tokens=777
    elif field=='context':context.user_id='bob'
    elif field=='fallback':config.fallback_models.append('alternate')
    controlled.state.release.set();await job
    original=await controlled.create(messages=[{'role':'user','content':'原始输入'}])
    assert original.from_cache
    assert {q.user_id:q.used_today for q in await sql(controlled,APIQuota)}=={'alice':18}
    assert all(r.user_id=='alice' for r in await sql(controlled,APICallLog))

async def test_reference_cleanup_when_all_waiters_cancelled_keeps_client_usable(controlled):
    leader=controlled.create();await asyncio.wait_for(controlled.state.entered.wait(),3)
    waiters=[controlled.create() for _ in range(5)];await asyncio.sleep(.03)
    for j in waiters:j.cancel()
    await asyncio.gather(*waiters,return_exceptions=True);leader.cancel()
    with pytest.raises(asyncio.CancelledError):await leader
    assert not getattr(controlled.api,'_generation_locks',{})
    controlled.state.release.set();result=await controlled.create()
    assert not result.from_cache and (await controlled.create()).from_cache
    assert (await sql(controlled,APIQuota))[0].used_today==18

@pytest.mark.parametrize('field',['messages','sampling','context'])
async def test_waiter_recovery_after_failed_leader_uses_original_enqueued_inputs(controlled,field):
    controlled.state.fail_first=True
    messages=[{'role':'user','content':'排队原始输入'}];config=GenerationConfig(model='primary',max_retries=0)
    context=RequestContext(user_id='alice')
    leader=controlled.create(messages=messages,config=config,context=context)
    await asyncio.wait_for(controlled.state.entered.wait(),3)
    waiter=controlled.create(messages=messages,config=config,context=context)
    await asyncio.sleep(.03)
    if field=='messages':messages[0]['content']='排队后修改'
    elif field=='sampling':config.max_tokens=777
    else:context.user_id='bob'
    controlled.state.release.set();results=await asyncio.gather(leader,waiter,return_exceptions=True)
    assert isinstance(results[0],AllModelsFailedError) and not isinstance(results[1],BaseException)
    assert controlled.state.calls[1]['messages']==[{'role':'user','content':'排队原始输入'}]
    assert controlled.state.calls[1]['config']['max_tokens']==4096
    assert {q.user_id:q.used_today for q in await sql(controlled,APIQuota)}=={'alice':18}
    assert (await controlled.create(messages=[{'role':'user','content':'排队原始输入'}])).from_cache

async def test_same_key_waiter_reuses_cache_after_leader_exhausts_quota(controlled):
    async with controlled.factory() as db:
        db.add(APIQuota(user_id='alice',daily_limit=18,used_today=0,total_used=0,reset_at=date.today()));await db.commit()
    jobs=[controlled.create(),controlled.create()];await settle_before_release(controlled)
    results=await asyncio.gather(*jobs,return_exceptions=True)
    assert all(not isinstance(r,BaseException) for r in results) and sum(r.from_cache for r in results)==1
    assert (await sql(controlled,APIQuota))[0].used_today==18 and len(controlled.state.calls)==1
    assert not controlled.api._generation_locks

async def test_quota_rejection_releases_key_and_all_waiters_can_retry(controlled):
    async with controlled.factory() as db:
        db.add(APIQuota(user_id='alice',daily_limit=1,used_today=1,total_used=1,reset_at=date.today()));await db.commit()
    results=await asyncio.gather(controlled.create(),controlled.create(),return_exceptions=True)
    assert all(isinstance(r,QuotaExceededError) for r in results) and not controlled.state.calls
    assert not controlled.api._generation_locks
    async with controlled.factory() as db:
        row=(await db.scalars(select(APIQuota))).one();row.daily_limit=100;await db.commit()
    controlled.state.release.set();assert not (await controlled.create()).from_cache
    assert (await sql(controlled,APIQuota))[0].used_today==19

async def test_real_cache_sql_failure_releases_key_and_recreated_table_recovers(controlled):
    async with controlled.engine.begin() as db:await db.execute(text('DROP TABLE api_cache'))
    results=await asyncio.gather(controlled.create(),controlled.create(),return_exceptions=True)
    assert all(isinstance(r,Exception) and 'no such table' in str(r) for r in results)
    assert not controlled.api._generation_locks and not controlled.state.calls
    async with controlled.engine.begin() as db:await db.run_sync(APICache.__table__.create)
    controlled.state.release.set();assert not (await controlled.create()).from_cache
    assert (await controlled.create()).from_cache and len(controlled.state.calls)==1
