"""Native scheduler/HTTP provider/JWT/one temporary SQLite DB; no paid model."""
import importlib, json, hashlib, os
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import select, func, event, text
from app.database import Base
from app.models import APICache, APIQuota, APICallLog, Conversation, Message
from app.api import chat
from app.core.assistant import Assistant
from app.core.api_scheduler.client import UnifiedAPIClient, GenerationConfig, AllModelsFailedError
from app.core.api_scheduler.adapters.openai import OpenAICompatAdapter
from tests.test_round53_chat_tenancy import tenancy, create, PATH
from tests.test_generation_cache_isolation_flow import cache_flow, rows, request
from tests.test_study_notification_flow import flow
from tests.test_reading_native_stream_http_flow import server

def save(name, evidence):
    out=Path(os.environ['KNOWALL_READING_NATIVE_EVIDENCE_DIR']);out.mkdir(parents=True,exist_ok=True)
    evidence['at']=datetime.now().astimezone().isoformat()
    evidence['scope']='native scheduler/quota/cache/SQL and localhost simulated provider; zero paid calls'
    (out/('context-'+hashlib.sha256(name.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')

@pytest.fixture
async def contextual(tenancy,monkeypatch):
    t=tenancy;engine=t.factory.kw['bind']
    @event.listens_for(engine.sync_engine,'connect')
    def timeout(connection,record):connection.execute('PRAGMA busy_timeout=200')
    async with engine.begin() as conn:
        await conn.execute(text('PRAGMA busy_timeout=200'))
        await conn.run_sync(Base.metadata.create_all,tables=[APICache.__table__,APIQuota.__table__,APICallLog.__table__])
    from app.core.api_scheduler import cache, client
    limiter=importlib.import_module('app.core.api_scheduler.rate_limiter')
    monkeypatch.setattr(cache,'async_session',t.factory);monkeypatch.setattr(limiter,'async_session',t.factory)
    monkeypatch.setattr(client,'rate_limiter',limiter.RateLimiter())
    unified=UnifiedAPIClient();monkeypatch.setattr(__import__('app.core.assistant',fromlist=['']), 'api_client',unified)
    monkeypatch.setattr(chat.ai_assistant,'chat_with_context',Assistant.chat_with_context.__get__(chat.ai_assistant))
    provider=FastAPI();calls=[];state=SimpleNamespace(reason='stop',content='完整回答',hook=None)
    @provider.post('/v1/chat/completions')
    async def simulation(req:Request):
        calls.append(await req.json())
        if state.hook:await state.hook()
        return {'choices':[{'message':{'content':state.content},'finish_reason':state.reason}],
            'usage':{'prompt_tokens':7,'completion_tokens':11},'model':'chosen-model'}
    async with server(provider) as remote:
        unified.configure_adapter('openai_alice','explicit-simulation',base_url=str(remote.base_url).rstrip('/')+'/v1',model_name='wrong-default')
        yield SimpleNamespace(**vars(t),unified=unified,state=state,provider_calls=calls)
    event.remove(engine.sync_engine,'connect',timeout)

@pytest.mark.parametrize('resume',[False,True])
@pytest.mark.parametrize('reason,content',[('length','半截'),('content_filter','半截'),('tool_calls','半截'),('unknown','半截'),(None,'半截'),('stop',''),('stop','   '),('stop','完整回答')])
async def test_native_context_completion_and_same_database(contextual,resume,reason,content,request):
    t=contextual;old=await create(t) if resume else None
    async with t.factory() as db:
        before=[(m.id,m.content) for m in (await db.scalars(select(Message))).all()]
        before_conv=await db.scalar(select(func.count()).select_from(Conversation))
    t.state.reason=reason;t.state.content=content
    payload={'message':'same-db contextual question','knowledge_context':'参考资料','model':'openai/chosen-model',**({'conversation_id':old} if old else {})}
    response=await t.client.post(PATH+'/assistant',headers=t.headers('alice'),json=payload)
    async with t.factory() as db:
        after=[(m.id,m.content) for m in (await db.scalars(select(Message))).all()]
        count=await db.scalar(select(func.count()).select_from(Conversation))
        cached=(await db.scalars(select(APICache))).all();logs=(await db.scalars(select(APICallLog))).all();quotas=(await db.scalars(select(APIQuota))).all()
    good=reason=='stop' and bool(content.strip())
    save(request.node.nodeid,{'response':{'status':response.status_code,'body':response.text},'calls':t.provider_calls,
        'before':before,'after':after,'cache_rows':len(cached),'logs':[{'success':l.success,'input':l.tokens_input,'output':l.tokens_output} for l in logs],
        'quota':[q.used_today for q in quotas],'same_database':True})
    assert len(t.provider_calls)==1,response.text
    assert t.provider_calls[0]['model']=='chosen-model'
    assert quotas[0].used_today==18 and len(logs)==1 and logs[0].success==good
    if good:
        assert response.status_code==200 and response.json()['message']==content
        assert len(after)==len(before)+2 and count==before_conv+(0 if resume else 1) and len(cached)==1
    else:
        assert response.status_code==500 and before==after and count==before_conv and not cached

@pytest.mark.parametrize('reason,content',[('length','半截'),('stop','   '),('tool_calls','半截')])
async def test_native_incomplete_scheduler_preserves_usage_without_fallback(cache_flow,reason,content,request):
    f=cache_flow;provider=FastAPI();calls=[]
    @provider.post('/v1/chat/completions')
    async def simulation(req:Request):
        calls.append(await req.json())
        return {'choices':[{'message':{'content':content},'finish_reason':reason}], 'usage':{'prompt_tokens':7,'completion_tokens':11}}
    async with server(provider) as remote:
        f.api._adapters.clear()
        f.api.configure_adapter('primary_alice','explicit-simulation',base_url=str(remote.base_url).rstrip('/')+'/v1',model_name='test')
        f.api.configure_adapter('alternate_alice','explicit-simulation',base_url=str(remote.base_url).rstrip('/')+'/v1',model_name='alternate')
        from tests.test_generation_cache_isolation_flow import request as arguments
        args=arguments();args['config'].fallback_models=['alternate'];args['config'].max_retries=3
        try:await f.api.generate(**args)
        except AllModelsFailedError:pass
        else:pytest.fail('incomplete provider result accepted as successful generation')
    quota=await rows(f,APIQuota);logs=await rows(f,APICallLog);cached=await rows(f,APICache)
    save(request.node.nodeid,{'calls':calls,'quota':quota[0].used_today,'logs':[{'success':l.success,'input':l.tokens_input,'output':l.tokens_output} for l in logs],'cache_rows':len(cached)})
    assert len(calls)==1 and quota[0].used_today==18 and not cached
    assert len(logs)==1 and logs[0].success is False and logs[0].tokens_input==7

async def test_context_deleted_during_generation_does_not_resurrect(contextual,request):
    t=contextual;conv=await create(t)
    async def remove():
        response=await t.client.delete(PATH+'/conversations/'+conv,headers=t.headers('alice'))
        assert response.status_code==200,response.text
    t.state.hook=remove
    response=await t.client.post(PATH+'/assistant',headers=t.headers('alice'),json={'message':'inflight','knowledge_context':'参考','model':'openai/chosen-model','conversation_id':conv})
    async with t.factory() as db:
        assert await db.get(Conversation,conv) is None
        assert not (await db.scalars(select(Message))).all()
    assert response.status_code==404,response.text


async def test_old_v4_incomplete_cache_preserved_but_not_reused(cache_flow,request):
    from tests.test_generation_cache_isolation_flow import request as arguments
    f=cache_flow;args=arguments();config=args['config'];context=args['context']
    provider=f.api.get_adapter(config.model,user_id=context.user_id)
    legacy={'version':4,'messages':args['messages'],'content':args['generation_content'],
        'template':args['prompt_template_id'],'task':args['task_type'].value,'model':config.model,
        'fallback':config.fallback_models,'sampling':[config.temperature,config.top_p,config.max_tokens],
        'scope':[context.user_id,context.project_id,context.api_key_level],
        'providers':[[config.model,f.api._adapter_cache_identity(provider)]]}
    key=hashlib.sha256(json.dumps(legacy,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    from app.core.api_scheduler.cache import cache_manager
    await cache_manager.set(cache_key=key,response_content='旧版半截',model_used='primary',tokens_input=7,tokens_output=11)
    result=await f.api.generate(**args);cached=await rows(f,APICache)
    save(request.node.nodeid,{'from_cache':result.from_cache,'content':result.content,'cache_rows':[{'key':r.cache_key,'content':r.response_content} for r in cached]})
    assert not result.from_cache and len(provider.calls)==1
    assert len(cached)==2 and any(r.cache_key==key and r.response_content=='旧版半截' for r in cached)


async def test_current_blank_cache_is_revalidated(cache_flow):
    from tests.test_generation_cache_isolation_flow import request as arguments
    f=cache_flow;args=arguments();first=await f.api.generate(**args)
    async with f.factory() as db:
        cached=(await db.scalars(select(APICache))).one();cached.response_content='   ';await db.commit()
    recovered=await f.api.generate(**args)
    assert not recovered.from_cache and recovered.content==first.content
    assert len(f.adapters['primary_alice'].calls)==2
