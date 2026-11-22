"""Durable receipts over localhost TCP, real JWT and disposable SQLite. Paid calls: 0."""
import asyncio
import importlib
import json
import hashlib
from pathlib import Path
from datetime import datetime
import pytest
from fastapi import FastAPI, Request
from sqlalchemy import select, func, event, create_engine, text, inspect
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from alembic import command
from alembic.config import Config
from app.models import Conversation, Message
from app.api import agent_runs
from app.config import settings
from tests.test_agent_readonly_http import native, configure, provider_reply, PATH
from tests.test_reading_native_stream_http_flow import server

OUT=Path(__file__).resolve().parents[2]/'docs/open-source-integration/native-agent-durable-2026-10-11/backend'

def record(request,data):
    OUT.mkdir(parents=True,exist_ok=True)
    data.update(test=request.node.nodeid,at=datetime.now().astimezone().isoformat(),paid_calls=0,
                auth='real JWT',database='disposable SQLite',provider='explicit localhost simulation')
    (OUT/(hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')

def run_model(): return importlib.import_module('app.models.agent_run').AgentRun

def provider(calls,*,fail=False,arrived=None,release=None):
    app=FastAPI()
    @app.post('/v1/chat/completions')
    async def model(req:Request):
        calls.append(await req.json())
        if arrived:
            arrived.set();await release.wait()
        if fail:return provider_reply('openai',text='unsupported [S99]')
        if len(calls)%2==1:return provider_reply('openai',name='read_document_chunk',args={'chunk_id':'a-chunk'})
        return provider_reply('openai',text='消耗氧气 [S1]')
    return app

async def ask(n,**extra):
    return await n.client.post(PATH+'/answer',headers=n.headers('alice'),json={'message':'解释细胞呼吸','model':'openai',**extra})

async def test_completed_receipt_matches_messages_and_survives_new_connection(native,request):
    n=native;calls=[]
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai');response=await ask(n)
    assert response.status_code==200,response.text
    body=response.json();assert body.get('run_id') and body.get('assistant_message_id')
    # Dispose the pool: the next request cannot rely on the preceding ORM identity map.
    await n.factory.kw['bind'].dispose()
    fresh_engine=create_async_engine(n.factory.kw['bind'].url)
    fresh_factory=async_sessionmaker(fresh_engine,expire_on_commit=False)
    try:
        async with fresh_factory() as connection:
            reconnected=await connection.get(run_model(),body['run_id'])
            assert reconnected.status=='completed' and reconnected.assistant_message_id==body['assistant_message_id']
    finally:await fresh_engine.dispose()
    receipt=await n.client.get(PATH+'/runs/'+body['run_id'],headers=n.headers('alice'))
    history=await n.client.get(PATH+'/conversations/'+body['conversation_id']+'/receipts',headers=n.headers('alice'))
    assert receipt.status_code==history.status_code==200
    r=receipt.json();h=history.json();assert r['status']=='completed'
    assert r['assistant_message_id']==body['assistant_message_id'] and r['steps']==body['steps']
    assert h['has_more'] is False and len(h['receipts'])==1
    assert h['receipts'][0]['sources'][0]['text']==r['sources'][0]['text']==''
    async with n.factory() as db:
        saved=await db.get(run_model(),body['run_id']);message=await db.get(Message,body['assistant_message_id'])
        assert saved.status=='completed' and message.role=='assistant' and message.content==body['message']
        assert saved.sources==body['sources'] and saved.conversation_id==message.conversation_id
    assert len(calls)==2
    record(request,{'post':body,'run':r,'history':h,'new_pool':True,'provider_calls':len(calls)})

@pytest.mark.parametrize('endpoint',['runs','receipts'])
@pytest.mark.parametrize('viewer',[None,'bob','disabled'])
async def test_receipts_never_leak_to_another_account(native,endpoint,viewer):
    n=native;calls=[]
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai');body=(await ask(n)).json()
    assert body.get('run_id')
    url=PATH+'/runs/'+body['run_id'] if endpoint=='runs' else PATH+'/conversations/'+body['conversation_id']+'/receipts'
    response=await n.client.get(url,headers=n.headers(viewer) if viewer else {})
    assert response.status_code==(401 if viewer is None else 403 if viewer=='disabled' else 404)
    assert '消耗氧气' not in response.text

async def test_running_is_committed_before_model_and_failure_persists_without_messages(native,request):
    n=native;calls=[];arrived=asyncio.Event();release=asyncio.Event()
    async with server(provider(calls,fail=True,arrived=arrived,release=release)) as remote:
        await configure(n,remote,'openai');pending=asyncio.create_task(ask(n))
        try:
            await asyncio.wait_for(arrived.wait(),5)
            async with n.factory() as db:
                running=(await db.scalars(select(run_model()))).one();assert running.status=='running'
                run_id=running.id
            live=await n.client.get(PATH+'/runs/'+run_id,headers=n.headers('alice'))
            assert live.status_code==200 and live.json()['status']=='running'
        finally:release.set()
        response=await pending
    assert response.status_code==502
    failed=await n.client.get(PATH+'/runs/'+run_id,headers=n.headers('alice'))
    assert failed.status_code==200 and failed.json()['status']=='failed'
    assert failed.json()['steps'][-1]['type']=='run_failed'
    assert response.json()['detail']['run_id']==run_id
    async with n.factory() as db:
        assert await db.scalar(select(func.count()).select_from(Message))==0
        assert await db.scalar(select(func.count()).select_from(Conversation))==0
    record(request,{'failure':failed.json(),'provider_calls':len(calls)})

async def test_actual_tcp_disconnect_saves_cancelled_terminal_not_completed(native,request):
    n=native;calls=[];arrived=asyncio.Event();release=asyncio.Event()
    async with server(provider(calls,arrived=arrived,release=release)) as remote:
        await configure(n,remote,'openai')
        reader,writer=await asyncio.open_connection(n.client.base_url.host,n.client.base_url.port)
        body=json.dumps({'message':'解释','model':'openai'}).encode()
        writer.write((f'POST {PATH}/answer HTTP/1.1\r\nHost: localhost\r\nAuthorization: {n.headers("alice")["Authorization"]}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n').encode()+body);await writer.drain()
        try:
            await asyncio.wait_for(arrived.wait(),5);writer.close();await writer.wait_closed()
            async with asyncio.timeout(3):
                while True:
                    async with n.factory() as db:
                        saved=(await db.scalars(select(run_model()))).one()
                        if saved.status=='cancelled':break
                    await asyncio.sleep(.03)
        finally:release.set();writer.close()
    assert saved.assistant_message_id is None and saved.steps[-1]['type']=='run_cancelled'
    assert [s['seq'] for s in saved.steps]==list(range(1,len(saved.steps)+1))
    async with n.factory() as db:assert await db.scalar(select(func.count()).select_from(Message))==0
    record(request,{'status':saved.status,'steps':saved.steps,'provider_calls':len(calls)})

async def test_final_commit_failure_rolls_back_messages_and_completed_receipt(native,request):
    n=native;calls=[]
    def fail_final_commit(session):
        # AgentRun startup and quota commits still work; fail the actual two-message commit.
        if any(isinstance(item,Message) for item in session.new) or any(
                isinstance(item,run_model()) and item.status=='completed' for item in session.dirty):
            raise SQLAlchemyError('disposable injected final commit failure')
    event.listen(Session,'before_commit',fail_final_commit)
    try:
        async with server(provider(calls)) as remote:
            await configure(n,remote,'openai');response=await ask(n)
    finally:event.remove(Session,'before_commit',fail_final_commit)
    assert response.status_code==503,response.text
    async with n.factory() as db:
        saved=(await db.scalars(select(run_model()))).one()
        assert saved.status=='failed' and saved.error_code=='PERSISTENCE_FAILED' and saved.assistant_message_id is None
        assert await db.scalar(select(func.count()).select_from(Message))==0
        assert await db.scalar(select(func.count()).select_from(Conversation))==0
    record(request,{'status':response.status_code,'run_status':saved.status,'messages':0})

async def test_deleted_owned_conversation_hides_run_and_history(native):
    n=native;calls=[]
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai');body=(await ask(n)).json()
    assert body.get('run_id')
    async with n.factory() as db:
        conv=await db.get(Conversation,body['conversation_id']);await db.delete(conv);await db.commit()
    assert (await n.client.get(PATH+'/runs/'+body['run_id'],headers=n.headers('alice'))).status_code==404
    assert (await n.client.get(PATH+'/conversations/'+body['conversation_id']+'/receipts',headers=n.headers('alice'))).status_code==404

def test_upgrade_old_database_adds_table_without_changing_existing_records(tmp_path,request):
    engine=create_engine('sqlite:///'+(tmp_path/'old.sqlite').as_posix())
    with engine.begin() as connection:
        connection.exec_driver_sql('CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)')
        connection.exec_driver_sql('CREATE TABLE conversations (id VARCHAR(36) PRIMARY KEY, user_id VARCHAR(36))')
        connection.exec_driver_sql('CREATE TABLE messages (id VARCHAR(36) PRIMARY KEY, content TEXT)')
        connection.exec_driver_sql("INSERT INTO users VALUES ('legacy-user')")
        connection.exec_driver_sql("INSERT INTO conversations VALUES ('legacy-conv','legacy-user')")
        connection.exec_driver_sql("INSERT INTO messages VALUES ('legacy-message','KEEP EXISTING CONTENT')")
        cfg=Config('alembic.ini');cfg.attributes['connection']=connection
        command.stamp(cfg,'202610102130');command.upgrade(cfg,'head')
        assert 'agent_runs' in inspect(connection).get_table_names()
        assert connection.execute(text('SELECT content FROM messages')).scalar_one()=='KEEP EXISTING CONTENT'
        assert connection.execute(text('SELECT user_id FROM conversations')).scalar_one()=='legacy-user'
        head=connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
        assert head!='202610102130'
        constraint=next(item['sqltext'] for item in inspect(connection).get_check_constraints('agent_runs') if item['name']=='ck_agent_runs_status')
        assert 'waiting_user' in constraint and 'partial' in constraint
        assert 'created' not in constraint and 'waiting_confirmation' not in constraint
        command.upgrade(cfg,'head')
        record(request,{'head':head,'legacy_messages_preserved':True,'upgrade_rerun':True})
    engine.dispose()


async def test_more_than_100_returns_latest_successful_receipts_only(native):
    from datetime import timedelta
    from app.models.agent_run import AgentRun,utc_now
    n=native;base=utc_now()
    async with n.factory() as db:
        db.add(Conversation(id='many',user_id='alice',role_preset='tutor'));await db.flush()
        for i in range(103):
            db.add(Message(id='answer-'+str(i),conversation_id='many',role='assistant',content='saved'))
            db.add(AgentRun(id='run-'+str(i),user_id='alice',conversation_id='many',assistant_message_id='answer-'+str(i),status='completed',model='simulated',sources=[{'text':'old excerpt'}],steps=[],created_at=base+timedelta(seconds=i)))
        db.add(AgentRun(id='failed-newer',user_id='alice',conversation_id='many',status='failed',model='simulated',sources=[],steps=[],created_at=base+timedelta(seconds=104)))
        await db.commit()
    response=await n.client.get(PATH+'/conversations/many/receipts',headers=n.headers('alice'))
    assert response.status_code==200,response.text
    body=response.json();assert body['has_more'] is True and len(body['receipts'])==100
    assert body['receipts'][0]['run_id']=='run-102' and body['receipts'][-1]['run_id']=='run-3'
    assert all(row['status']=='completed' and row['sources'][0]['text']=='' for row in body['receipts'])


async def test_missing_run_and_conversation_are_404_and_old_plain_chat_has_empty_receipts(native):
    n=native
    assert (await n.client.get(PATH+'/runs/missing',headers=n.headers('alice'))).status_code==404
    assert (await n.client.get(PATH+'/conversations/missing/receipts',headers=n.headers('alice'))).status_code==404
    async with n.factory() as db:
        db.add(Conversation(id='plain',user_id='alice',role_preset='tutor'));await db.commit()
    response=await n.client.get(PATH+'/conversations/plain/receipts',headers=n.headers('alice'))
    assert response.json()=={'receipts':[],'has_more':False}


async def test_missing_receipt_table_fails_before_any_provider_call(native):
    n=native;calls=[]
    async with n.factory.kw['bind'].begin() as connection:
        await connection.run_sync(run_model().__table__.drop)
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai');response=await ask(n)
    assert response.status_code==503,response.text
    assert calls==[]


async def test_cancel_during_startup_commit_does_not_leave_started_run_or_call_model(native,monkeypatch):
    store=importlib.import_module('app.core.agent_runtime.store')
    n=native;calls=[];arrived=asyncio.Event();release=asyncio.Event();original=store.start
    async def held_start(*args):
        run_id=await original(*args);arrived.set();await release.wait();return run_id
    monkeypatch.setattr(store,'start',held_start)
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai')
        reader,writer=await asyncio.open_connection(n.client.base_url.host,n.client.base_url.port)
        body=json.dumps({'message':'解释','model':'openai'}).encode()
        writer.write((f'POST {PATH}/answer HTTP/1.1\r\nHost: localhost\r\nAuthorization: {n.headers("alice")["Authorization"]}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n').encode()+body);await writer.drain()
        try:
            await asyncio.wait_for(arrived.wait(),5);writer.close();await writer.wait_closed()
            await asyncio.sleep(.05);release.set()
            async with asyncio.timeout(3):
                while True:
                    async with n.factory() as db:
                        row=(await db.scalars(select(run_model()))).one()
                        if row.status=='cancelled':break
                    await asyncio.sleep(.03)
        finally:release.set();writer.close()
    assert calls==[] and row.steps[-1]['type']=='run_cancelled'


async def test_quota_failure_is_durable_and_keeps_original_429_contract(native,monkeypatch):
    n=native;calls=[];monkeypatch.setattr(settings,'daily_token_limit',0)
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai');response=await ask(n)
    assert response.status_code==429 and isinstance(response.json()['detail'],str)
    async with n.factory() as db:
        row=(await db.scalars(select(run_model()))).one()
        assert row.status=='failed' and row.error_code=='QUOTA_EXCEEDED' and row.steps[-1]['type']=='run_failed'
        assert await db.scalar(select(func.count()).select_from(Message))==0
    assert calls==[]


async def test_failure_in_existing_conversation_is_readable_but_not_success_history(native):
    n=native;calls=[]
    async with n.factory() as db:
        db.add(Conversation(id='owned',user_id='alice',role_preset='tutor'));await db.commit()
    async with server(provider(calls,fail=True)) as remote:
        await configure(n,remote,'openai');response=await ask(n,conversation_id='owned')
    assert response.status_code==502
    body=response.json();assert body['detail'].get('run_id')
    r=await n.client.get(PATH+'/runs/'+body['detail']['run_id'],headers=n.headers('alice'))
    h=await n.client.get(PATH+'/conversations/owned/receipts',headers=n.headers('alice'))
    assert r.json()['status']=='failed' and h.json()=={'receipts':[],'has_more':False}


@pytest.mark.parametrize('status',['queued','running','waiting_user','completed','partial','failed','cancelled'])
async def test_sql_status_constraint_accepts_only_specification_states(native,status):
    async with native.factory() as db:
        db.add(run_model()(user_id='alice',status=status,model='explicit-simulated',sources=[],steps=[]))
        await db.commit()


@pytest.mark.parametrize('status',['created','waiting_confirmation','invented'])
async def test_sql_status_constraint_rejects_non_specification_states(native,status):
    async with native.factory() as db:
        db.add(run_model()(user_id='alice',status=status,model='explicit-simulated',sources=[],steps=[]))
        with pytest.raises(SQLAlchemyError):await db.commit()
        await db.rollback()
