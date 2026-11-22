"""Production UI refresh -> real JWT/SQL receipts; no paid provider."""
import asyncio,importlib,json,os
from pathlib import Path
from datetime import datetime
import pytest
from fastapi import FastAPI,Request
from sqlalchemy import select
from app.database import get_db
from app.models import Message,APIQuota,APICallLog
from tests.test_agent_readonly_http import native,provider_reply
from tests.test_reading_native_stream_http_flow import server


@pytest.mark.integration
async def test_current_production_durable_history(native):
    n=native;calls=[];provider=FastAPI()
    @provider.post('/v1/chat/completions')
    async def simulation(req:Request):
        payload=await req.json();calls.append(payload)
        assert payload['stream'] is False
        if len(calls)==1:return provider_reply('openai',name='search_documents',args={'query':'细胞呼吸'},call_id='search')
        assert '私有秘密' not in json.dumps(payload,ensure_ascii=False)
        if len(calls)==2:return provider_reply('openai',name='read_document_chunk',args={'chunk_id':'a-chunk'},call_id='read')
        return provider_reply('openai',text='资料中的细胞呼吸消耗氧气 [S1]')
    app=importlib.import_module('app.main').app
    previous=dict(app.dependency_overrides)
    async def database():
        async with n.factory() as db:yield db
    app.dependency_overrides[get_db]=database
    out=Path(__file__).resolve().parents[2]/'docs/open-source-integration/native-agent-durable-2026-10-11/native-browser'
    out.mkdir(parents=True,exist_ok=True)
    try:
        async with server(provider) as remote,server(app) as backend:
            n.unified.configure_adapter('openai_alice','explicit-simulated-no-paid-key',base_url=str(remote.base_url).rstrip('/')+'/v1',model_name='simulated-native',owner_user_id='alice')
            frontend=Path(__file__).resolve().parents[2]/'frontend'
            env=dict(os.environ,AGENT_NATIVE_BASE=str(backend.base_url).rstrip('/'),AGENT_NATIVE_TOKEN=n.headers('alice')['Authorization'][7:],PLAYWRIGHT_BROWSERS_PATH=str(frontend.parents[1]/'.playwright-browsers'))
            process=await asyncio.create_subprocess_exec('node','node_modules/@playwright/test/cli.js','test','--config=playwright.agent-durable.config.ts','agent-durable-native.spec.ts','--reporter=list',cwd=frontend,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
            try:stdout,_=await asyncio.wait_for(process.communicate(),100)
            except TimeoutError:
                process.kill();await process.wait();raise
            (out/'playwright.log').write_bytes(stdout)
            assert process.returncode==0,stdout.decode(errors='replace')[-6000:]
        from app.models.agent_run import AgentRun
        async with n.factory() as db:
            messages=(await db.scalars(select(Message))).all()
            quota=(await db.scalars(select(APIQuota))).one()
            logs=(await db.scalars(select(APICallLog))).all()
            runs=(await db.scalars(select(AgentRun))).all()
        assert len(calls)==3 and len(messages)==2 and quota.used_today==54 and len(logs)==3
        assert len(runs)==1 and runs[0].status=='completed'
        assert runs[0].assistant_message_id in {m.id for m in messages if m.role=='assistant'}
        (out/'sql-provider.json').write_text(json.dumps({'at':datetime.now().astimezone().isoformat(),'scope':'real main.app/JWT/temp SQLite; current production browser; explicit local model simulator','paid_calls':0,'provider_requests':calls,'message_count':len(messages),'used_tokens':quota.used_today,'log_count':len(logs),'runs':[{'id':r.id,'status':r.status,'assistant_message_id':r.assistant_message_id} for r in runs]},ensure_ascii=False,indent=2),encoding='utf8')
    finally:
        app.dependency_overrides.clear();app.dependency_overrides.update(previous)
