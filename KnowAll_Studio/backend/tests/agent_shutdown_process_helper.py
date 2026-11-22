"""Private isolated-process harness; production lifespan/startup are unchanged."""
import asyncio
import json
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from sqlalchemy import select, text

from app import main, database
from app.core.auth import create_access_token
from app.core.api_scheduler import api_client
from app.core.api_scheduler.adapters import openai, http_utils
from app.core.agent_runtime.tool_bridge import DocumentToolBridge
from app.models import User, Document, DocumentChunk

control=Path(sys.argv[1]);origin=sys.argv[2];mode=sys.argv[3]
original_tool=DocumentToolBridge.execute
blocked=[]

def local_only(url,headers,payload,timeout):
    if not url.startswith(origin+'/'):
        blocked.append(url);raise RuntimeError('External model request blocked in isolated process')
    return http_utils.sync_post(url,headers,payload,timeout)
openai.sync_post=local_only

async def tool(self,*args):
    if len(args)>2 and args[2].get('chunk_id')=='held-chunk':
        (control/'tool-entered.json').write_text(json.dumps({'pid':os.getpid()}),encoding='utf8')
        try:await asyncio.Event().wait()
        finally:
            async with httpx.AsyncClient(trust_env=False,timeout=15) as client:
                response=await client.post(origin+'/cleanup')
                response.raise_for_status()
            (control/'tool-cleaned.json').write_text(json.dumps({'pid':os.getpid()}),encoding='utf8')
    return await original_tool(self,*args)
if mode=='run':DocumentToolBridge.execute=tool

@asynccontextmanager
async def isolated_lifespan(app):
    # Execute actual init_db (fresh bootstrap/stamp, then upgrade on second PID),
    # actual main.startup_init, WS activation and all production close hooks.
    async with main.lifespan(app):
        async with database.async_session() as db:
            if mode=='run':
                db.add(User(id='alice',username='alice',email='alice@test.invalid',password_hash='unused',is_active=True))
                db.add(Document(id='a',filename='source.txt',file_type='txt',sha256='explicit-local-source',metadata_={'knowledge_owner_id':'alice'}))
                await db.flush()
                db.add_all([DocumentChunk(id='a-chunk',doc_id='a',chunk_index=0,text_content='细胞呼吸消耗氧气',page_range='p3'),
                            DocumentChunk(id='held-chunk',doc_id='a',chunk_index=1,text_content='明确本机等待工具',page_range='p4')])
                await db.commit()
            revision=await db.scalar(text('SELECT version_num FROM alembic_version'))
            journal=await db.scalar(text('PRAGMA journal_mode'))
        api_client.configure_adapter('openai_alice','explicit-local-simulated-no-paid-key',origin+'/v1','simulated-native')
        (control/(mode+'-ready.json')).write_text(json.dumps({'pid':os.getpid(),'url':base,'token':create_access_token('alice'),
            'startup':'actual main.lifespan/startup_init/init_db; no startup mocks','revision':revision,
            'journal_mode':journal,'database_url':database.settings.database_url,'paid_calls':0}),encoding='utf8')
        yield
    (control/(mode+'-closed.json')).write_text(json.dumps({'pid':os.getpid(),'blocked_external_requests':blocked,'production_lifespan_exited':True}),encoding='utf8')

main.app.router.lifespan_context=isolated_lifespan
listener=socket.socket();listener.bind(('127.0.0.1',0));base='http://127.0.0.1:'+str(listener.getsockname()[1])
service=uvicorn.Server(uvicorn.Config(main.app,log_level='warning',lifespan='on',timeout_graceful_shutdown=4))

@main.app.post('/__test_shutdown')
async def shutdown():
    service.should_exit=True
    return {'requested':True,'pid':os.getpid()}

if __name__=='__main__':
    try:asyncio.run(service.serve(sockets=[listener]))
    finally:listener.close()
