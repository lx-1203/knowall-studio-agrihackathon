"""Native JWT, temporary SQL and localhost HTTP. All model responses simulated."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from jose import jwt
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.api import chat
from app.config import settings
from app.core.auth import create_access_token
from app.database import Base, get_db
from app.models import User, Conversation, Message
from tests.test_reading_native_stream_http_flow import server

PATH='/api/v1/chat'
SEND=['assistant','assistant/stream','assistant/rag','assistant/rag/stream','assistant/graphrag','assistant/graphrag/stream']

@pytest.fixture
async def tenancy(tmp_path, monkeypatch):
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'chat.sqlite').as_posix())
    async with engine.begin() as c:
        await c.run_sync(lambda db: Base.metadata.create_all(db,tables=[User.__table__,Conversation.__table__,Message.__table__]))
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as db:
        db.add_all([User(id=u,username=u,email=u+'@test.invalid',password_hash='test-only',is_active=u!='disabled') for u in ['alice','bob','disabled']]); await db.commit()
    monkeypatch.setattr(settings,'jwt_secret','round53-disposable-test-secret')
    monkeypatch.setattr(settings,'desktop_require_login',False)
    import app.database
    monkeypatch.setattr(app.database,'async_session',factory)
    calls=[]
    async def no_keys(*args): pass
    async def response(*args,**kw): calls.append(kw['user_id']); return '完整模拟回答'
    async def stream(*args,**kw): calls.append(kw['user_id']); yield '完整模拟回答'
    monkeypatch.setattr(chat,'load_user_api_keys',no_keys)
    for name in ['chat','chat_with_context']: monkeypatch.setattr(chat.ai_assistant,name,response)
    for name in ['chat_stream','chat_stream_with_context']: monkeypatch.setattr(chat.ai_assistant,name,stream)
    monkeypatch.setattr(chat.rag_assistant,'chat_with_rag',response); monkeypatch.setattr(chat.rag_assistant,'chat_stream_with_rag',stream)
    app=FastAPI(); app.include_router(chat.router)
    async def database():
        async with factory() as db: yield db
    app.dependency_overrides[get_db]=database
    try:
        async with server(app) as live:
            async with httpx.AsyncClient(base_url=live.base_url,trust_env=False,timeout=10) as client:
                yield SimpleNamespace(client=client,factory=factory,calls=calls,headers=lambda u:{'Authorization':'Bearer '+create_access_token(u)})
    finally: await engine.dispose()

async def create(t, mode='assistant', user='alice', **body):
    r=await t.client.post(PATH+'/'+mode,headers=t.headers(user) if user else {},json={'message':'account private',**body})
    assert r.status_code==200,r.text
    if mode.endswith('stream'):
        events=[json.loads(line[6:]) for line in r.text.splitlines() if line.startswith('data: ')]
        assert not any('error' in e for e in events)
        return events[-1]['conversation_id']
    return r.json()['conversation_id']

@pytest.mark.parametrize('mode',SEND)
async def test_create_resume_and_block_foreign_owner(tenancy,mode):
    t=tenancy; conv=await create(t,mode); before=len(t.calls)
    r=await t.client.post(PATH+'/'+mode,headers=t.headers('bob'),json={'message':'steal','conversation_id':conv})
    assert r.status_code==404,r.text
    assert len(t.calls)==before
    await create(t,mode,conversation_id=conv)
    async with t.factory() as db:
        assert (await db.get(Conversation,conv)).user_id=='alice'
        assert await db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id==conv))==4
        assert not await db.scalar(select(Message.id).where(Message.content=='steal'))

@pytest.mark.parametrize('operation',['list','get','rename','delete'])
@pytest.mark.parametrize('stranger',['bob',None])
async def test_management_owner_and_missing_are_indistinguishable(tenancy,operation,stranger):
    t=tenancy; conv=await create(t); headers=t.headers(stranger) if stranger else {}
    if operation=='list':
        r=await t.client.get(PATH+'/conversations',headers=headers); assert r.status_code==200
        assert r.json()==[]
    else:
        method={'get':'get','rename':'put','delete':'delete'}[operation]
        kwargs={'json':{'title':'stolen'}} if method=='put' else {}
        r=await getattr(t.client,method)(PATH+'/conversations/'+conv,headers=headers,**kwargs)
        missing=await getattr(t.client,method)(PATH+'/conversations/no-such-id',headers=headers,**kwargs)
        assert r.status_code==missing.status_code==404 and r.json()==missing.json()
    async with t.factory() as db:
        assert (await db.get(Conversation,conv)).title=='account private'
        assert await db.scalar(select(func.count()).select_from(Message))==2

async def test_owner_crud_pagination_and_legacy_local_identity(tenancy):
    t=tenancy; own=await create(t); other=await create(t,user='bob'); local=await create(t,user=None)
    listed=await t.client.get(PATH+'/conversations?limit=1&offset=0',headers=t.headers('alice'))
    assert [c['id'] for c in listed.json()]==[own]
    assert (await t.client.get(PATH+'/conversations?limit=1&offset=1',headers=t.headers('alice'))).json()==[]
    assert [c['id'] for c in (await t.client.get(PATH+'/conversations')).json()]==[local]
    assert (await t.client.get(PATH+'/conversations/'+local,headers=t.headers('alice'))).status_code==404
    assert (await t.client.put(PATH+'/conversations/'+own,headers=t.headers('alice'),json={'title':' new '})).json()['title']=='new'
    assert len((await t.client.get(PATH+'/conversations/'+own,headers=t.headers('alice'))).json()['messages'])==2
    assert (await t.client.delete(PATH+'/conversations/'+own,headers=t.headers('alice'))).status_code==200
    async with t.factory() as db:
        assert await db.get(Conversation,own) is None
        assert not (await db.scalars(select(Message).where(Message.conversation_id==own))).all()
        assert await db.get(Conversation,other) is not None
        assert (await db.get(Conversation,local)).user_id=='local_user'

@pytest.mark.parametrize('bad',['invalid','expired','disabled','missing-user','basic','empty-header','required-missing'])
@pytest.mark.parametrize('operation',['list','send'])
async def test_invalid_credentials_never_fall_back_to_local(tenancy,bad,operation,monkeypatch):
    t=tenancy; await create(t,user=None); before=len(t.calls)
    token=create_access_token(bad)
    if bad=='expired': token=jwt.encode({'sub':'alice','exp':datetime.now(timezone.utc)-timedelta(seconds=10)},settings.jwt_secret,algorithm=settings.jwt_algorithm)
    headers={'Authorization':'Bearer '+token}
    if bad=='invalid': headers={'Authorization':'Bearer broken'}
    if bad=='basic': headers={'Authorization':'Basic broken'}
    if bad=='empty-header': headers={'Authorization':''}
    if bad=='required-missing': monkeypatch.setattr(settings,'desktop_require_login',True); headers={}
    r=await t.client.get(PATH+'/conversations',headers=headers) if operation=='list' else await t.client.post(PATH+'/assistant/stream',headers=headers,json={'message':'wrong'})
    assert r.status_code==(403 if bad=='disabled' else 401),r.text
    assert len(t.calls)==before

async def test_nonstream_pipeline_failure_is_one_request_and_rolls_back(tenancy,monkeypatch):
    from app.core import rag_assistant as module
    from app.core.rag_assistant import RAGAssistant
    t=tenancy; calls=[]; seen=[]; real=RAGAssistant()
    class Pipeline:
        async def search(self,*a,**kw):return SimpleNamespace(context='native reference')
    def pipeline(name):seen.append(name);return Pipeline()
    monkeypatch.setattr(module,'get_pipeline_by_name',pipeline)
    async def broken(*args,**kwargs):calls.append(kwargs['user_id']);raise RuntimeError('explicit model simulation failed')
    monkeypatch.setattr(module.base_assistant,'chat',broken)
    monkeypatch.setattr(settings,'rag_provider','llamaindex')
    monkeypatch.setattr(chat,'rag_assistant',real)
    response=await t.client.post(PATH+'/assistant/rag',headers=t.headers('alice'),json={'message':'generation fail'})
    assert response.status_code==500 and calls==['alice']
    assert seen==['llamaindex']
    async with t.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Conversation))==0
        assert await db.scalar(select(func.count()).select_from(Message))==0

@pytest.mark.parametrize('mode',['assistant/rag','assistant/rag/stream'])
async def test_configured_native_pipeline_is_reached_by_real_route(tenancy,monkeypatch,mode):
    from app.core import rag_assistant as module
    from app.core.rag_assistant import RAGAssistant
    t=tenancy; seen=[]; generated=[]
    class Pipeline:
        async def search(self,*a,**kw):return SimpleNamespace(context='配置参考')
    def pipeline(name):seen.append(name);return Pipeline()
    async def generate(*args,**kwargs):generated.append((args,kwargs));return '配置完整回答'
    async def stream(*args,**kwargs):generated.append((args,kwargs));yield '配置完整回答'
    monkeypatch.setattr(module,'get_pipeline_by_name',pipeline)
    monkeypatch.setattr(module.base_assistant,'chat',generate);monkeypatch.setattr(module.base_assistant,'chat_stream',stream)
    monkeypatch.setattr(settings,'rag_provider','llamaindex');monkeypatch.setattr(chat,'rag_assistant',RAGAssistant())
    conv=await create(t,mode)
    assert seen==['llamaindex'] and len(generated)==1
    assert generated[0][1]['user_id']=='alice' and '配置参考' in generated[0][0][0]
    async with t.factory() as db:
        assert (await db.get(Conversation,conv)).user_id=='alice'
        assert (await db.scalar(select(Message).where(Message.conversation_id==conv,Message.role=='assistant'))).content=='配置完整回答'
