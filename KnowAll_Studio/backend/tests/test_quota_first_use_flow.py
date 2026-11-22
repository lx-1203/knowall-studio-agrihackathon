"""Real isolated SQLite/scheduler; AI explicitly simulated, no hard reservation claim."""
import asyncio,hashlib,json,os
from pathlib import Path
from datetime import datetime,date,timedelta
import pytest
from app.config import settings
from app.models import APIQuota,APICache,APICallLog
from app.core.api_scheduler import client
from app.core.api_scheduler.client import TaskType,GenerationConfig,RequestContext,QuotaExceededError
from tests.test_generation_cache_isolation_flow import cache_flow,flow,rows
from tests.test_quota_concurrency_flow import synchronize_two_quota_reads

@pytest.fixture
async def quota_flow(cache_flow,monkeypatch,request,tmp_path):
    f=cache_flow;monkeypatch.setattr(settings,'daily_token_limit',50)
    monkeypatch.setenv('NO_PROXY','127.0.0.1,localhost');monkeypatch.setenv('no_proxy','127.0.0.1,localhost')
    root=Path(__file__).resolve().parents[1]
    paths=['app/core/api_scheduler/rate_limiter.py','app/core/api_scheduler/client.py','app/core/api_scheduler/cache.py',
        'app/core/agents/language_agent.py','app/api/language.py','app/api/admin.py','app/models/__init__.py','app/config.py']
    hashes=lambda:{p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}
    d={'Test':request.node.nodeid,'Start':datetime.now().astimezone().isoformat(),'SourceBefore':hashes(),
        'AI':'explicit in-process simulation; actual SQLite/cache/quota/scheduler','DB':str(f.database)}
    f.evidence=d
    try:yield f
    finally:
        d['End']=datetime.now().astimezone().isoformat();d['SourceAfter']=hashes()
        d['AdapterCalls']={k:len(v.calls) for k,v in f.adapters.items()}
        await f.engine.dispose()
        for suffix in ('','-wal','-shm'):
            p=Path(str(f.database)+suffix);assert p.resolve().parent==tmp_path.resolve()
            if p.exists():p.unlink()
        d['DBRemoved']=not f.database.exists()
        out=Path(os.environ.get('KNOWALL_QUOTA_EVIDENCE_DIR',str(tmp_path/'quota-evidence')));out.mkdir(parents=True,exist_ok=True)
        (out/(hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf8')

def args(user='alice',content='x'*40):
    return dict(task_type=TaskType.CHAT,messages=[{'role':'user','content':content}],generation_content=content,
        prompt_template_id='quota-first-use-explicit-simulation',config=GenerationConfig(model='primary',max_retries=0,fallback_models=[]),context=RequestContext(user_id=user))

@pytest.mark.parametrize('limit,estimate,allowed',[(0,1,False),(5,6,False),(50,51,False),(50,50,True),(50,49,True),(0,0,True)])
async def test_first_use_obeys_same_budget_boundary_as_existing_row(quota_flow,monkeypatch,limit,estimate,allowed):
    f=quota_flow;monkeypatch.setattr(settings,'daily_token_limit',limit)
    first=await client.rate_limiter.check_quota('alice',estimate)
    f.evidence['First']={'Limit':limit,'Estimate':estimate,'Decision':first}
    assert first[0] is allowed,first
    second=await client.rate_limiter.check_quota('alice',estimate);assert second==first
    q=(await rows(f,APIQuota))[0];assert q.daily_limit==limit and q.used_today==q.total_used==0
    assert await rows(f,APICache)==await rows(f,APICallLog)==[]

@pytest.mark.parametrize('limit',[0,9])
async def test_first_scheduler_call_above_limit_never_reaches_adapter_then_recovers(quota_flow,monkeypatch,limit):
    f=quota_flow;monkeypatch.setattr(settings,'daily_token_limit',limit)
    with pytest.raises(QuotaExceededError):await f.api.generate(**args())
    assert len(f.adapters['primary_alice'].calls)==0
    assert await rows(f,APICache)==await rows(f,APICallLog)==[]
    async with f.factory() as db:
        q=await db.get(APIQuota,(await rows(f,APIQuota))[0].id);q.daily_limit=50;await db.commit()
    recovered=await f.api.generate(**args());assert not recovered.from_cache
    cached=await f.api.generate(**args());assert cached.from_cache and cached.content==recovered.content
    assert len(f.adapters['primary_alice'].calls)==1
    q=(await rows(f,APIQuota))[0];assert q.used_today==q.total_used==18
    assert len(await rows(f,APICache))==len(await rows(f,APICallLog))==1
    f.evidence['Recovery']={'Quota':18,'Cached':cached.from_cache}

@pytest.mark.parametrize('limit,estimate,allowed',[(1,2,False),(2,2,True),(0,1,False)])
async def test_real_first_row_unique_conflict_rechecks_budget(quota_flow,monkeypatch,limit,estimate,allowed):
    f=quota_flow;monkeypatch.setattr(settings,'daily_token_limit',limit)
    with monkeypatch.context() as scope:
        synchronize_two_quota_reads(scope)
        outcomes=await asyncio.wait_for(asyncio.gather(client.rate_limiter.check_quota('alice',estimate),client.rate_limiter.check_quota('alice',estimate),return_exceptions=True),5)
    f.evidence['Concurrent']={'Limit':limit,'Estimate':estimate,'Decisions':[str(x) for x in outcomes]}
    assert all(isinstance(x,tuple) and x[0] is allowed for x in outcomes),outcomes
    assert len(await rows(f,APIQuota))==1 and (await rows(f,APIQuota))[0].used_today==0

@pytest.mark.parametrize('previous_day',[False,True])
@pytest.mark.parametrize('estimate,allowed',[(10,True),(50,True),(51,False)])
async def test_existing_and_previous_day_budget_status_is_consistent(quota_flow,previous_day,estimate,allowed):
    f=quota_flow
    async with f.factory() as db:
        db.add(APIQuota(user_id='alice',daily_limit=50,used_today=120 if previous_day else 0,total_used=150,reset_at=date.today()-timedelta(days=1) if previous_day else date.today()));await db.commit()
    assert (await client.rate_limiter.get_quota_status('alice'))=={'daily_limit':50,'used_today':0,'remaining':50}
    decision=await client.rate_limiter.check_quota('alice',estimate);assert decision[0] is allowed
    status=await client.rate_limiter.get_quota_status('alice');assert status['used_today']==0 and status['remaining']==50
    q=(await rows(f,APIQuota))[0];assert q.total_used==150
    if allowed:
        await client.rate_limiter.record_usage('alice',7,11)
        q=(await rows(f,APIQuota))[0];assert q.used_today==18 and q.total_used==168 and q.reset_at==date.today()
    f.evidence['Decision']=decision;f.evidence['BeforeUsageStatus']=status

async def test_failed_response_usage_and_cache_hits_keep_current_quota_contract(quota_flow):
    f=quota_flow
    request=args();request['config'].response_validator=lambda content: False
    from app.core.api_scheduler.client import AllModelsFailedError
    with pytest.raises(AllModelsFailedError):await f.api.generate(**request)
    assert (await rows(f,APIQuota))[0].used_today==18 and not (await rows(f,APICallLog))[0].success
    assert await rows(f,APICache)==[]
    request['config'].response_validator=lambda content:True
    recovered=await f.api.generate(**request);assert not recovered.from_cache
    async with f.factory() as db:
        q=await db.get(APIQuota,(await rows(f,APIQuota))[0].id);q.daily_limit=0;await db.commit()
    assert (await f.api.generate(**request)).from_cache
    assert (await rows(f,APIQuota))[0].used_today==36
    f.evidence['Usage']={'Used':36,'FailureLogged':True,'CacheHitAllowedAfterLimitLowered':True}

async def test_previous_day_scheduler_usage_does_not_inherit_old_daily_usage(quota_flow):
    f=quota_flow
    async with f.factory() as db:
        db.add(APIQuota(user_id='alice',daily_limit=50,used_today=120,total_used=150,reset_at=date.today()-timedelta(days=1)));await db.commit()
    assert not (await f.api.generate(**args())).from_cache
    assert (await f.api.generate(**args())).from_cache
    q=(await rows(f,APIQuota))[0];assert q.used_today==18 and q.total_used==168
    f.evidence['Usage']={'Used':18,'Total':168,'Reset':str(q.reset_at)}
