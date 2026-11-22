"""Current built browser, real background API/main/JWT/SQL, local gated provider."""
import asyncio,importlib,json,os
from pathlib import Path
from datetime import datetime
import pytest
from fastapi import FastAPI,Request
from sqlalchemy import select
from app.database import get_db
from app.models import AgentRun,Message,APICallLog
from tests.test_agent_readonly_http import native,provider_reply
from tests.test_reading_native_stream_http_flow import server


@pytest.mark.integration
async def test_production_background_disconnect_and_cancel(native):
    n=native
    from app.models.agent_background_task import AgentBackgroundTask
    async with n.factory() as db:
        async with db.bind.begin() as conn: await conn.run_sync(AgentBackgroundTask.__table__.create,checkfirst=True)
    calls=[];gate=asyncio.Event();provider=FastAPI()
    @provider.post('/v1/chat/completions')
    async def simulation(request:Request):
        payload=await request.json();calls.append(payload)
        assert payload['stream'] is False
        last=payload['messages'][-1]
        assert '私有秘密' not in json.dumps(payload,ensure_ascii=False)
        if last['role']=='user':return provider_reply('openai',name='search_documents',args={'query':'细胞呼吸'},call_id='search')
        if last.get('tool_call_id')=='search':
            await gate.wait()
            return provider_reply('openai',name='read_document_chunk',args={'chunk_id':'a-chunk'},call_id='read')
        return provider_reply('openai',text='资料中的细胞呼吸消耗氧气 [S1]')
    @provider.post('/release')
    async def release():gate.set();return {'released':True}
    @provider.post('/reset')
    async def reset():gate.clear();return {'reset':True}
    @provider.get('/state')
    async def state():
        async with n.factory() as db:
            runs=(await db.scalars(select(AgentRun).order_by(AgentRun.created_at))).all()
            controls=(await db.scalars(select(AgentBackgroundTask))).all()
            messages=(await db.scalars(select(Message))).all()
        return {'controls':[{'run_id':c.run_id,'cancel_requested':c.cancel_requested} for c in controls],'message_count':len(messages),'provider_requests':len(calls),'runs':[{'id':row.id,'status':row.status,'steps':row.steps,'assistant_message_id':row.assistant_message_id} for row in runs]}
    app=importlib.import_module('app.main').app;previous=dict(app.dependency_overrides)
    async def database():
        async with n.factory() as db:yield db
    app.dependency_overrides[get_db]=database
    out=Path(__file__).resolve().parents[2]/'docs/open-source-integration/native-agent-background-2026-10-11/native-browser'
    out.mkdir(parents=True,exist_ok=True)
    try:
        async with server(provider) as remote,server(app) as backend:
            n.unified.configure_adapter('openai_alice','explicit-local-simulated-key',base_url=str(remote.base_url).rstrip('/')+'/v1',model_name='simulated-native',owner_user_id='alice')
            frontend=Path(__file__).resolve().parents[2]/'frontend'
            env=dict(os.environ,AGENT_NATIVE_BASE=str(backend.base_url).rstrip('/'),AGENT_NATIVE_CONTROL=str(remote.base_url).rstrip('/'),AGENT_NATIVE_TOKEN=n.headers('alice')['Authorization'][7:],PLAYWRIGHT_BROWSERS_PATH=str(frontend.parents[1]/'.playwright-browsers'))
            process=await asyncio.create_subprocess_exec('node','node_modules/@playwright/test/cli.js','test','--config=playwright.agent-background-native.config.ts',cwd=frontend,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
            try:stdout,_=await asyncio.wait_for(process.communicate(),125)
            except TimeoutError:
                process.kill();await process.wait();raise
            (out/'playwright.log').write_bytes(stdout)
            assert process.returncode==0,stdout.decode(errors='replace')[-7000:]
            gate.set()
        async with n.factory() as db:
            runs=(await db.scalars(select(AgentRun))).all();controls=(await db.scalars(select(AgentBackgroundTask))).all()
            messages=(await db.scalars(select(Message))).all()
            logs=(await db.scalars(select(APICallLog))).all()
        assert len(runs)==2 and len(controls)==2 and sorted(row.status for row in runs)==['cancelled','completed']
        cancelled=next(row for row in runs if row.status=='cancelled')
        assert next(c for c in controls if c.run_id==cancelled.id).cancel_requested is True
        assert len(messages)==2 and len(calls)==5
        completed=next(row for row in runs if row.status=='completed')
        assert completed.assistant_message_id in {m.id for m in messages if m.role=='assistant'}
        (out/'sql-provider.json').write_text(json.dumps({'at':datetime.now().astimezone().isoformat(),'paid_calls':0,'scope':'actual built browser/Vite proxy/main/JWT/background API/temp SQL; provider gated explicitly simulated','provider_requests':len(calls),'messages':len(messages),'api_call_logs':len(logs),'controls':[{'run_id':c.run_id,'cancel_requested':c.cancel_requested} for c in controls],'runs':[{'id':r.id,'status':r.status,'steps':r.steps} for r in runs]},ensure_ascii=False,indent=2),encoding='utf8')
    finally:
        gate.set()
        from app.api import agent_runs
        await agent_runs.background_manager.close()
        app.dependency_overrides.clear();app.dependency_overrides.update(previous)
