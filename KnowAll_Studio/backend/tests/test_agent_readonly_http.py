"""Real localhost JWT -> agent API -> native adapter -> simulated provider -> SQL.

No real/paid model calls. Receipts identify this explicitly.
"""
import importlib,json,hashlib,os,copy
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
import httpx,pytest
from fastapi import FastAPI,Request
from sqlalchemy import select,func,update
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.database import Base,get_db
from app.config import settings
from app.models import User,Document,DocumentChunk,Conversation,Message,APIQuota,APICallLog,APICache,AgentRun
from app.core.auth import create_access_token
from app.core.api_scheduler.client import UnifiedAPIClient
from app.core.api_scheduler.adapters.openai import OpenAICompatAdapter
from app.core.api_scheduler.adapters.anthropic import AnthropicAdapter
from app.api import agent_runs
from tests.test_reading_native_stream_http_flow import server

PATH='/api/v1/agent'


@pytest.fixture
async def native(tmp_path,monkeypatch):
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'agent.sqlite').as_posix())
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all,tables=[User.__table__,Document.__table__,DocumentChunk.__table__,
            Conversation.__table__,Message.__table__,APIQuota.__table__,APICallLog.__table__,APICache.__table__,AgentRun.__table__])
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as db:
        for user in ['alice','bob','disabled']:
            db.add(User(id=user,username=user,email=user+'@test.invalid',password_hash='unused',is_active=user!='disabled'))
        for name,owner,text in [('a','alice','细胞呼吸消耗氧气'),('b','bob','细胞呼吸私有秘密')]:
            db.add(Document(id=name,filename=name+'.txt',file_type='txt',sha256=name,metadata_={'knowledge_owner_id':owner}))
            await db.flush()
            db.add(DocumentChunk(id=name+'-chunk',doc_id=name,chunk_index=0,text_content=text,page_range='p3'))
        await db.commit()
    monkeypatch.setattr(settings,'jwt_secret','native-agent-test-disposable-secret')
    monkeypatch.setattr(settings,'daily_token_limit',100000)
    database=importlib.import_module('app.database')
    rate=importlib.import_module('app.core.api_scheduler.rate_limiter')
    client=importlib.import_module('app.core.api_scheduler.client')
    monkeypatch.setattr(database,'async_session',factory)
    monkeypatch.setattr(rate,'async_session',factory)
    monkeypatch.setattr(client,'rate_limiter',rate.RateLimiter())
    unified=UnifiedAPIClient()
    monkeypatch.setattr(agent_runs,'api_client',unified)
    async def keys(*a): pass
    monkeypatch.setattr(agent_runs,'load_user_api_keys',keys)
    app=FastAPI();app.include_router(agent_runs.router)
    async def db():
        async with factory() as session:yield session
    app.dependency_overrides[get_db]=db
    headers=lambda user:{'Authorization':'Bearer '+create_access_token(user)}
    try:
        async with server(app) as backend:
            async with httpx.AsyncClient(base_url=backend.base_url,timeout=15,trust_env=False) as transport:
                yield SimpleNamespace(client=transport,factory=factory,unified=unified,headers=headers,app=app)
    finally:await engine.dispose()


def provider_reply(protocol,text='',name=None,args=None,call_id='c1'):
    if protocol=='openai':
        message={'role':'assistant','content':text if not name else None}
        if name:message['tool_calls']=[{'id':call_id,'type':'function','function':{'name':name,'arguments':json.dumps(args or {},ensure_ascii=False)}}]
        return {'model':'simulated-native','choices':[{'message':message,'finish_reason':'tool_calls' if name else 'stop'}],
                'usage':{'prompt_tokens':7,'completion_tokens':11}}
    blocks=[{'type':'text','text':text}] if text else []
    if name:blocks.append({'type':'tool_use','id':call_id,'name':name,'input':args or {}})
    return {'model':'simulated-native','content':blocks,'stop_reason':'tool_use' if name else 'end_turn',
            'usage':{'input_tokens':7,'output_tokens':11}}


async def configure(n,remote,protocol):
    cls=OpenAICompatAdapter if protocol=='openai' else AnthropicAdapter
    adapter=cls('explicit-simulated-no-paid-key',base_url=str(remote.base_url).rstrip('/')+'/v1',model_name='simulated-native')
    n.unified._adapters[protocol+'_alice']=adapter


def receipt(request,n,response,calls,extra=None):
    out=Path(os.environ.get('KNOWALL_AGENT_EVIDENCE_DIR',str(Path(__file__).resolve().parents[2]/'docs/open-source-integration/native-agent-readonly-2026-10-11/http')))
    out.mkdir(parents=True,exist_ok=True)
    data={'at':datetime.now().astimezone().isoformat(),'test':request.node.nodeid,'status':response.status_code,
          'body':response.json(),'provider_requests':calls,'provider_scope':'localhost explicitly simulated model; native HTTP adapter',
          'database':'temporary SQLite','auth':'real JWT/User dependency','paid_calls':0,**(extra or {})}
    (out/('http-'+hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')


@pytest.mark.parametrize('protocol',['openai','anthropic'])
async def test_actual_tool_search_read_model_roundtrip_persists_only_grounded_answer(native,protocol,request):
    n=native;provider=FastAPI();calls=[]
    @provider.post('/v1/chat/completions')
    @provider.post('/v1/messages')
    async def model(req:Request):
        payload=await req.json();calls.append(payload)
        assert payload['stream'] is False and len(payload['tools'])==2
        if len(calls)==1:return provider_reply(protocol,name='search_documents',args={'query':'细胞呼吸'},call_id='search')
        assert 'a-chunk' in json.dumps(payload,ensure_ascii=False) and '私有秘密' not in json.dumps(payload,ensure_ascii=False)
        if len(calls)==2:return provider_reply(protocol,name='read_document_chunk',args={'chunk_id':'a-chunk'},call_id='read')
        return provider_reply(protocol,text='资料中的细胞呼吸消耗氧气 [S1]')
    async with server(provider) as remote:
        await configure(n,remote,protocol)
        response=await n.client.post(PATH+'/answer',headers=n.headers('alice'),json={'message':'解释细胞呼吸','model':protocol})
    async with n.factory() as db:
        messages=(await db.scalars(select(Message))).all();quota=(await db.scalars(select(APIQuota))).one()
        logs=(await db.scalars(select(APICallLog))).all();cache=(await db.scalars(select(APICache))).all()
    receipt(request,n,response,calls,{'messages':len(messages),'usage':quota.used_today,'logs':len(logs),'cache':len(cache)})
    assert response.status_code==200,response.text
    body=response.json()
    assert body['status']=='completed' and body['sources'][0]['chunk_id']=='a-chunk'
    assert body['sources'][0]['text']=='细胞呼吸消耗氧气' and body['sources'][0]['page_range']=='p3'
    assert len(calls)==3 and len(messages)==2 and quota.used_today==54 and len(logs)==3 and not cache
    assert 'raw_content' not in body and 'assistant_message' not in body
    assert [s['type'] for s in body['steps']].count('tool_completed')==2


@pytest.mark.parametrize('mode',['no-source','invented-citation','loop','foreign-source','bad-arguments','duplicate-id','source-deleted'])
async def test_incomplete_tools_never_save_a_completed_conversation(native,mode,request):
    n=native;provider=FastAPI();calls=[]
    @provider.post('/v1/chat/completions')
    async def model(req:Request):
        payload=await req.json();calls.append(payload)
        if mode=='no-source':return provider_reply('openai',text='我已经读完 [S1]')
        if mode=='loop':return provider_reply('openai',name='search_documents',args={'query':'细胞'},call_id='c'+str(len(calls)))
        if len(calls)==1:
            args={'chunk_id':'b-chunk'} if mode=='foreign-source' else {'chunk_id':'a-chunk','user_id':'bob'} if mode=='bad-arguments' else {'chunk_id':'a-chunk'}
            return provider_reply('openai',name='read_document_chunk',args=args)
        if mode=='duplicate-id':return provider_reply('openai',name='read_document_chunk',args={'chunk_id':'a-chunk'})
        if mode=='source-deleted':
            async with n.factory() as db:
                chunk=await db.get(DocumentChunk,'a-chunk');await db.delete(chunk);await db.commit()
        return provider_reply('openai',text='已经解释 [S99]' if mode=='invented-citation' else '已经解释 [S1]')
    async with server(provider) as remote:
        await configure(n,remote,'openai')
        response=await n.client.post(PATH+'/answer',headers=n.headers('alice'),json={'message':'解释','model':'openai'})
    async with n.factory() as db:
        messages=await db.scalar(select(func.count()).select_from(Message))
        convs=await db.scalar(select(func.count()).select_from(Conversation))
    receipt(request,n,response,calls,{'message_rows':messages,'conversation_rows':convs})
    assert response.status_code==502,response.text
    assert messages==convs==0 and len(calls)<=4
    assert response.json()['detail']['code']!='MODEL_REQUEST_FAILED',response.text


@pytest.mark.parametrize('user',[None,'bob','disabled','missing'])
async def test_native_api_auth_and_unconfigured_model_have_no_provider_effect(native,user):
    n=native; headers=n.headers(user) if user else {}
    response=await n.client.post(PATH+'/answer',headers=headers,json={'message':'解释','model':'openai'})
    assert response.status_code==(401 if user in (None,'missing') else 403 if user=='disabled' else 400)
    async with n.factory() as db:assert not (await db.scalars(select(APIQuota))).all()


async def test_capabilities_are_real_and_unknown_models_not_reported_supported(native):
    n=native
    response=await n.client.get(PATH+'/capabilities?model=missing',headers=n.headers('alice'))
    assert response.status_code==200,response.text
    body=response.json()
    assert body['available'] is False and body['tools']==[] and body['streaming_tools'] is False
    assert (await n.client.get(PATH+'/capabilities')).status_code==401


async def test_configured_capabilities_show_only_allowlisted_read_tools_without_inference(native,request):
    n=native
    n.unified.configure_adapter('openai_alice','explicit-test-key',base_url='http://127.0.0.1:9/v1',owner_user_id='alice')
    response=await n.client.get(PATH+'/capabilities?model=gpt-4o-mini',headers=n.headers('alice'))
    assert response.status_code==200
    body=response.json()
    assert body['available'] is True and body['model_tool_support']=='unverified'
    assert {tool['function']['name'] for tool in body['tools']}=={'search_documents','read_document_chunk'}
    assert body['durable_runs'] is False and body['streaming_tools'] is False
    async with n.factory() as db:assert not (await db.scalars(select(APIQuota))).all()
    receipt(request,n,response,[],{'capability_query_provider_calls':0})


async def test_foreign_conversation_checked_before_native_provider_selection(native):
    n=native
    async with n.factory() as db:
        db.add(Conversation(id='other',title='Bob',user_id='bob',role_preset='tutor'));await db.commit()
    response=await n.client.post(PATH+'/answer',headers=n.headers('alice'),json={'message':'steal','model':'openai','conversation_id':'other'})
    assert response.status_code==404
    async with n.factory() as db:assert not (await db.scalars(select(APIQuota))).all()


async def test_current_main_really_mounts_native_routes_without_startup_or_paid_calls(native,monkeypatch,request):
    from app.main import app
    n=native
    async def disposable_database():
        async with n.factory() as db:yield db
    monkeypatch.setitem(app.dependency_overrides,get_db,disposable_database)
    async with server(app) as main:
        schema=(await main.get('/openapi.json')).json()
        response=await main.post(PATH+'/answer',json={'message':'no credentials'})
    assert PATH+'/answer' in schema['paths'] and PATH+'/capabilities' in schema['paths']
    assert response.status_code==401
    receipt(request,n,response,[],{'main_app_mount':True,'startup':'lifespan disabled; no real user-data changes'})


async def test_deleted_conversation_during_model_await_stays_deleted(native,request):
    n=native;provider=FastAPI();calls=[]
    async with n.factory() as db:
        db.add(Conversation(id='owned',title='Alice',user_id='alice',role_preset='tutor'));await db.commit()
    @provider.post('/v1/chat/completions')
    async def model(req:Request):
        calls.append(await req.json())
        if len(calls)==1:return provider_reply('openai',name='read_document_chunk',args={'chunk_id':'a-chunk'})
        async with n.factory() as db:
            conv=await db.get(Conversation,'owned');await db.delete(conv);await db.commit()
        return provider_reply('openai',text='消耗氧气 [S1]')
    async with server(provider) as remote:
        await configure(n,remote,'openai')
        response=await n.client.post(PATH+'/answer',headers=n.headers('alice'),json={'message':'解释','model':'openai','conversation_id':'owned'})
    assert response.status_code==404
    async with n.factory() as db:
        assert await db.get(Conversation,'owned') is None
        assert not (await db.scalars(select(Message))).all()
    receipt(request,n,response,calls,{'deleted_conversation_resurrected':False})


@pytest.mark.parametrize('first_tool',['shell','read_document_chunk'])
async def test_unknown_or_bad_tool_can_recover_using_real_allowed_source(native,first_tool,request):
    n=native;provider=FastAPI();calls=[]
    @provider.post('/v1/chat/completions')
    async def model(req:Request):
        payload=await req.json();calls.append(payload)
        if len(calls)==1:return provider_reply('openai',name=first_tool,args={'user_id':'bob'},call_id='bad')
        if len(calls)==2:
            assert 'UNKNOWN_TOOL' in str(payload) if first_tool=='shell' else 'INVALID_ARGUMENTS' in str(payload)
            return provider_reply('openai',name='read_document_chunk',args={'chunk_id':'a-chunk'},call_id='good')
        return provider_reply('openai',text='消耗氧气 [S1]')
    async with server(provider) as remote:
        await configure(n,remote,'openai')
        response=await n.client.post(PATH+'/answer',headers=n.headers('alice'),json={'message':'解释','model':'openai'})
    assert response.status_code==200,response.text
    assert [step['type'] for step in response.json()['steps']].count('tool_failed')==1
    assert len(calls)==3 and response.json()['sources'][0]['document_id']=='a'
    receipt(request,n,response,calls)


async def test_quota_exhausted_between_tool_turns_returns_incomplete_without_history(native,request):
    n=native;provider=FastAPI();calls=[]
    @provider.post('/v1/chat/completions')
    async def model(req:Request):
        calls.append(await req.json())
        async with n.factory() as db:
            quota=(await db.scalars(select(APIQuota))).one();quota.daily_limit=18;await db.commit()
        return provider_reply('openai',name='read_document_chunk',args={'chunk_id':'a-chunk'})
    async with server(provider) as remote:
        await configure(n,remote,'openai')
        response=await n.client.post(PATH+'/answer',headers=n.headers('alice'),json={'message':'解释','model':'openai'})
    assert response.status_code==429 and len(calls)==1
    async with n.factory() as db:
        assert not (await db.scalars(select(Message))).all()
        assert (await db.scalars(select(APIQuota))).one().used_today==18
        assert len((await db.scalars(select(APICallLog))).all())==1
    receipt(request,n,response,calls)
