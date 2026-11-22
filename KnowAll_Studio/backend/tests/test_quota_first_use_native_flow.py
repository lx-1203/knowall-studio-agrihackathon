"""Real TCP/JWT/quota/cache; only localhost AI replies are explicitly simulated."""
import json
from datetime import date,timedelta
from contextlib import asynccontextmanager
import pytest
from fastapi import FastAPI,Request
from app.api import language,admin
from app.config import settings
from app.database import get_db
from app.models import APIQuota,APICache,APICallLog,LanguageVocabulary
from app.core.api_scheduler.adapters.openai import OpenAICompatAdapter
from tests.test_quota_first_use_flow import quota_flow,cache_flow,flow,rows
from tests.test_reading_native_stream_http_flow import server

@asynccontextmanager
async def endpoints(f,monkeypatch):
    d=f.evidence;d['BackendHTTP']=[];d['ProviderHTTP']=[];d['GenerationEstimate']=[]
    d['AI']='explicit localhost simulated AI; actual API/JWT/SQLite/quota/cache'
    actual=f.api.generate
    async def generated(**kwargs):
        estimate=len(kwargs['generation_content'])//4
        d['GenerationEstimate'].append(estimate)
        kwargs['config'].max_retries=0;kwargs['config'].fallback_models=[]
        return await actual(**kwargs)
    monkeypatch.setattr(f.api,'generate',generated)
    provider=FastAPI()
    @provider.post('/v1/chat/completions')
    async def simulation(req:Request):
        auth=req.headers['authorization'];assert auth in ('Bearer alice-quota-local-key','Bearer bob-quota-local-key')
        d['ProviderHTTP'].append({'URL':str(req.url),'User':'alice' if 'alice-' in auth else 'bob','Payload':await req.json(),'Simulation':True})
        return {'choices':[{'message':{'content':json.dumps([{'word':'energy','definition':'明确模拟能量'}])}}],'usage':{'prompt_tokens':7,'completion_tokens':11}}
    backend=FastAPI();backend.include_router(language.router);backend.include_router(admin.router)
    async def db():
        async with f.factory() as session:yield session
    backend.dependency_overrides[get_db]=db
    async with server(provider) as remote:
        f.api._adapters.clear()
        for user in ('alice','bob'):
            f.api._adapters['deepseek-chat_'+user]=OpenAICompatAdapter(user+'-quota-local-key',str(remote.base_url).rstrip('/')+'/v1','quota-test')
        async with server(backend) as client:
            async def send(method,path,user='alice',**kwargs):
                r=await client.request(method,path,headers=getattr(f,user),**kwargs)
                d['BackendHTTP'].append({'Method':method,'URL':str(r.request.url),'User':user,'Status':r.status_code,'Body':r.text});return r
            yield send
            d['ClosedPorts']=[remote.base_url.port,client.base_url.port]

async def generate(send,user='alice'):
    return await send('POST','/api/v1/language/vocabulary/generate',user,json={'document_id':'source','summary_id':'summary'})
async def status(send,user='alice'):
    r=await send('GET','/api/v1/admin/quota/status',user);assert r.status_code==200,r.text;return r.json()

@pytest.mark.parametrize('limit',[0,1])
async def test_native_first_use_over_limit_is_blocked_then_same_request_recovers(quota_flow,monkeypatch,limit):
    f=quota_flow;monkeypatch.setattr(settings,'daily_token_limit',limit)
    async with endpoints(f,monkeypatch) as send:
        assert await status(send)=={'daily_limit':limit,'used_today':0,'remaining':limit}
        rejected=await generate(send)
        assert f.evidence['GenerationEstimate'][0]>limit
        assert rejected.status_code==500 and 'Daily token limit reached' in rejected.text,rejected.text
        assert f.evidence['ProviderHTTP']==[]
        assert await rows(f,APICache)==await rows(f,APICallLog)==await rows(f,LanguageVocabulary)==[]
        assert await status(send)=={'daily_limit':limit,'used_today':0,'remaining':limit}
        async with f.factory() as db:
            q=await db.get(APIQuota,(await rows(f,APIQuota))[0].id);q.daily_limit=50;await db.commit()
        for _ in range(2):
            r=await generate(send);assert r.status_code==200 and r.json()['status']=='success',r.text
        assert len(f.evidence['ProviderHTTP'])==1
        assert await status(send)=={'daily_limit':50,'used_today':18,'remaining':32}
        assert len(await rows(f,APICache))==len(await rows(f,APICallLog))==len(await rows(f,LanguageVocabulary))==1

async def test_native_quota_and_provider_are_scoped_to_signed_accounts(quota_flow,monkeypatch):
    f=quota_flow;monkeypatch.setattr(settings,'daily_token_limit',0)
    async with f.factory() as db:
        db.add(APIQuota(user_id='bob',daily_limit=50,used_today=0,total_used=0,reset_at=date.today()));await db.commit()
    async with endpoints(f,monkeypatch) as send:
        r=await generate(send);assert r.status_code==500 and f.evidence['ProviderHTTP']==[]
        r=await generate(send,'bob');assert r.status_code==200,r.text
        assert await status(send)=={'daily_limit':0,'used_today':0,'remaining':0}
        assert await status(send,'bob')=={'daily_limit':50,'used_today':18,'remaining':32}
        async with f.factory() as db:
            quotas=await rows(f,APIQuota);q=await db.get(APIQuota,next(x.id for x in quotas if x.user_id=='alice'));q.daily_limit=50;await db.commit()
        for user in ('alice','bob','alice'):
            assert (await generate(send,user)).status_code==200
        assert [x['User'] for x in f.evidence['ProviderHTTP']]==['bob','alice']
        assert {q.user_id:q.used_today for q in await rows(f,APIQuota)}=={'alice':18,'bob':18}
        assert len(await rows(f,APICache))==2
        f.evidence['Scope']='signed quota/provider/cache scope; shared vocabulary is unchanged and is not personal state isolation'

async def test_native_previous_day_status_and_usage_reset_at_real_generation(quota_flow,monkeypatch):
    f=quota_flow
    async with f.factory() as db:
        db.add(APIQuota(user_id='alice',daily_limit=50,used_today=120,total_used=150,reset_at=date.today()-timedelta(days=1)));await db.commit()
    async with endpoints(f,monkeypatch) as send:
        assert await status(send)=={'daily_limit':50,'used_today':0,'remaining':50}
        for _ in range(2):assert (await generate(send)).status_code==200
        assert await status(send)=={'daily_limit':50,'used_today':18,'remaining':32}
        q=(await rows(f,APIQuota))[0];assert q.used_today==18 and q.total_used==168 and q.reset_at==date.today()
        assert len(f.evidence['ProviderHTTP'])==1
        f.evidence['Usage']={'Daily':18,'Total':168,'Reset':str(q.reset_at)}
