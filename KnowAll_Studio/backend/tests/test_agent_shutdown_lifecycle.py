"""Orderly Agent shutdown: real SQL/JWT/TCP; explicit local provider only."""
import asyncio
import hashlib
import importlib
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi import Request
import httpx
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api import agent_runs
from app.core.agent_runtime import store
from app.core.agent_runtime.background import BackgroundRunManager
from app.models import AgentRun, Message, AgentBackgroundTask
from tests.test_agent_readonly_http import native, configure, PATH
from tests.test_agent_readonly_http import provider_reply
from tests.test_agent_background_runs import provider, submit, terminal
from tests.test_reading_native_stream_http_flow import server

OUT=Path(__file__).resolve().parents[2]/'docs/open-source-integration/native-agent-shutdown-2026-10-11/backend'

def record(request, **data):
    OUT.mkdir(parents=True,exist_ok=True)
    data.update(test=request.node.nodeid,at=datetime.now(timezone.utc).isoformat(),paid_calls=0)
    (OUT/('shutdown-'+hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:16]+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')

@pytest.fixture
async def shutdown_native(native,monkeypatch):
    manager=BackgroundRunManager()
    monkeypatch.setattr(agent_runs,'background_manager',manager)
    async with native.factory.kw['bind'].begin() as db:
        await db.run_sync(AgentBackgroundTask.__table__.create)
    try:yield native
    finally:await manager.close()

async def test_closed_post_is_503_after_auth_and_creates_no_run_or_model(shutdown_native,request):
    n=shutdown_native;manager=agent_runs.background_manager;await manager.close()
    calls=[]
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai')
        response=await n.client.post(PATH+'/runs',headers=n.headers('alice'),json={'message':'关闭中','model':'openai'})
        unauth=await n.client.post(PATH+'/runs',json={'message':'关闭中','model':'openai'})
    assert response.status_code==503 and response.json()['detail']=='服务正在关闭，任务未启动'
    assert unauth.status_code==401 and calls==[]
    async with n.factory() as db:
        assert await db.scalar(select(func.count()).select_from(AgentRun))==0
        assert await db.scalar(select(func.count()).select_from(AgentBackgroundTask))==0
    record(request,status=response.status_code,unauthenticated=unauth.status_code,provider_calls=0,run_rows=0)

async def test_close_owns_commit_to_launch_race_even_if_http_caller_is_cancelled(shutdown_native,monkeypatch,request):
    n=shutdown_native;manager=agent_runs.background_manager;accepted=asyncio.Event();release=asyncio.Event();calls=[]
    original=store.start_background;run_ids=[]
    async def commit_then_hold(*args):
        run_id=await original(*args);run_ids.append(run_id);accepted.set();await release.wait();return run_id
    monkeypatch.setattr(store,'start_background',commit_then_hold)
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai')
        # Direct coroutine uses the real route/preflight/SQL, while native HTTP
        # below proves the gate; cancellation is an HTTP-response owner loss.
        async with n.factory() as db:
            from types import SimpleNamespace
            caller=asyncio.create_task(agent_runs.start_background(agent_runs.AgentAnswerRequest(message='竞争',model='openai'),db,SimpleNamespace(id='alice')))
            await asyncio.wait_for(accepted.wait(),3)
            closer=asyncio.create_task(manager.close());await asyncio.sleep(0)
            caller.cancel();await asyncio.gather(caller,return_exceptions=True)
            try:
                assert not closer.done(), 'close abandoned accepted SQL submission before launch'
                response=await n.client.post(PATH+'/runs',headers=n.headers('alice'),json={'message':'关闭中','model':'openai'})
                assert response.status_code==503
            finally:release.set();await asyncio.wait_for(closer,4)
        row=(await n.client.get(PATH+'/runs/'+run_ids[0],headers=n.headers('alice'))).json()
    assert row['status']=='cancelled' and row['cancel_requested'] is False
    assert not manager._jobs and calls==[]
    record(request,receipt=row,accepted_submission_survived_caller=True,provider_calls=0)

@pytest.mark.parametrize('cancel_first',[False,True])
async def test_concurrent_close_awaits_tool_finally_and_caller_cancel_does_not_abandon(shutdown_native,monkeypatch,request,cancel_first):
    n=shutdown_native;manager=agent_runs.background_manager;entered=asyncio.Event();cleanup=asyncio.Event();release=asyncio.Event();cleaned=asyncio.Event();calls=[]
    async def hold(self,*args):
        entered.set()
        try:await asyncio.Event().wait()
        finally:cleanup.set();await release.wait();cleaned.set()
    monkeypatch.setattr(agent_runs.DocumentToolBridge,'execute',hold)
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai');run_id=await submit(n);await asyncio.wait_for(entered.wait(),3)
        first=asyncio.create_task(manager.close());await asyncio.wait_for(cleanup.wait(),3)
        second=asyncio.create_task(manager.close());await asyncio.sleep(0)
        if cancel_first:first.cancel();await asyncio.gather(first,return_exceptions=True)
        try:
            assert not second.done() and not cleaned.is_set()
            async with n.factory() as db:assert (await db.get(AgentRun,run_id)).status=='running'
            with pytest.raises(RuntimeError):manager.activate()
        finally:release.set();await asyncio.wait_for(second,4);await asyncio.gather(first,return_exceptions=True)
        await manager.close();row=await terminal(n,run_id)
    assert cleaned.is_set() and row['status']=='cancelled' and row['cancel_requested'] is False and len(calls)==1
    assert not manager._jobs
    manager.activate();assert manager.accepting
    record(request,receipt=row,caller_cancelled=cancel_first,cleanup_before_terminal=True,provider_calls=len(calls))

async def test_close_rejects_direct_launch_without_constructing_executor(shutdown_native):
    manager=agent_runs.background_manager;await manager.close();called=[]
    def execute():called.append(True);return asyncio.sleep(0)
    with pytest.raises(RuntimeError):manager.launch('not-accepted','alice',shutdown_native.factory,execute)
    assert not called and not manager._jobs

async def test_terminal_sql_failure_stays_truthfully_running_unconfirmed(shutdown_native,monkeypatch,request):
    n=shutdown_native;entered=asyncio.Event();cleaned=asyncio.Event();calls=[]
    async def hold(self,*args):
        entered.set()
        try:await asyncio.Event().wait()
        finally:cleaned.set()
    monkeypatch.setattr(agent_runs.DocumentToolBridge,'execute',hold)
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai');run_id=await submit(n);await asyncio.wait_for(entered.wait(),3)
        def fail(session):
            if any(isinstance(row,AgentRun) and row.status in ('cancelled','failed') for row in session.dirty):raise SQLAlchemyError('explicit private terminal write failure')
        event.listen(Session,'before_commit',fail)
        try:await agent_runs.background_manager.close()
        finally:event.remove(Session,'before_commit',fail)
        row=(await n.client.get(PATH+'/runs/'+run_id,headers=n.headers('alice'))).json()
    assert cleaned.is_set() and row['status']=='running' and row['recovery_state']=='active'
    assert row['cancel_requested'] is False and not agent_runs.background_manager._jobs and len(calls)==1
    # Recovery is a timestamp heuristic, not live supervisor inspection. Make
    # this isolated snapshot stale without sleeping or inventing a terminal.
    async with n.factory() as db:
        saved=await db.get(AgentRun,run_id);past=datetime.now(timezone.utc)-timedelta(minutes=6)
        saved.created_at=past
        saved.steps=[{**step,'at':past.isoformat()} for step in saved.steps]
        await db.commit()
    stale=(await n.client.get(PATH+'/runs/'+run_id,headers=n.headers('alice'))).json()
    assert stale['status']=='running' and stale['recovery_state']=='unconfirmed'
    record(request,receipt=row,stale_receipt=stale,terminal_sql_failure=True,cleanup_finished=True,stale_timestamp_test=True)

async def test_close_preserves_completed_message_steps_and_sources(shutdown_native,request):
    n=shutdown_native;calls=[]
    async with server(provider(calls)) as remote:
        await configure(n,remote,'openai');run_id=await submit(n);before=await terminal(n,run_id)
    await agent_runs.background_manager.close()
    after=(await n.client.get(PATH+'/runs/'+run_id,headers=n.headers('alice'))).json()
    assert before==after and after['status']=='completed'
    result=await n.client.get(PATH+'/runs/'+run_id+'/result',headers=n.headers('alice'))
    assert result.status_code==200 and len(calls)==2
    record(request,receipt=after,result=result.json(),terminal_unchanged=True)

@pytest.mark.parametrize('failure',['none','body','existing-cleanup'])
async def test_actual_main_lifespan_activates_and_always_closes_agent(monkeypatch,tmp_path,failure):
    monkeypatch.setenv('KNOWALL_LOG_DIR',str(tmp_path/'logs'))
    import app.main as main
    import app.websocket as ws
    import app.core.agents.research_agent as research
    import app.core.book.compiler as book
    import app.core.knowledge_authority as authority
    manager=BackgroundRunManager();await manager.close()
    monkeypatch.setattr(agent_runs,'background_manager',manager)
    monkeypatch.setattr(main,'startup_init',AsyncMock())
    monkeypatch.setattr(ws,'start_background_tasks',lambda:None)
    stopped=AsyncMock();rclose=AsyncMock(side_effect=RuntimeError('existing close failure') if failure=='existing-cleanup' else None)
    monkeypatch.setattr(ws,'stop_background_tasks',stopped)
    monkeypatch.setattr(research.research_agent,'close',rclose)
    monkeypatch.setattr(book.book_compiler,'close',AsyncMock())
    monkeypatch.setattr(authority,'close_source_searches',AsyncMock())
    async def use():
        async with main.lifespan(FastAPI()):
            assert manager.accepting, 'main did not reopen a fully drained manager'
            if failure=='body':raise RuntimeError('body failure')
    if failure=='none':await use()
    else:
        with pytest.raises(RuntimeError,match='body failure' if failure=='body' else 'existing close failure'):await use()
    assert not manager.accepting and not manager._jobs and stopped.await_count==rclose.await_count==1

async def test_actual_process_lifespan_shutdown_then_new_pid_reads_same_saved_sql(tmp_path,request):
    control=tmp_path/'process';control.mkdir();database_file=control/'isolated.sqlite'
    cleanup_entered=asyncio.Event();cleanup_release=asyncio.Event();calls=[];remote=FastAPI();processes=[];logs=[]
    @remote.post('/v1/chat/completions')
    async def reply(req:Request):
        body=await req.json();calls.append(body)
        last=body['messages'][-1]
        if last['role']=='user':return provider_reply('openai',name='read_document_chunk',args={'chunk_id':'held-chunk' if '等待' in last['content'] else 'a-chunk'})
        return provider_reply('openai',text='细胞呼吸消耗氧气 [S1]')
    @remote.post('/cleanup')
    async def cleanup():cleanup_entered.set();await cleanup_release.wait();return {'released':True}
    async def wait_file(path,process):
        async with asyncio.timeout(45):
            while not path.exists():
                assert process.returncode is None, 'isolated service exited during actual startup; inspect process log'
                await asyncio.sleep(.025)
        return json.loads(path.read_text(encoding='utf8'))
    async def launch(mode,origin):
        env=dict(os.environ,KNOWALL_IGNORE_ENV_FILE='1',DATABASE_URL='sqlite+aiosqlite:///'+database_file.as_posix(),
                 JWT_SECRET='isolated-process-shutdown-secret',GRAPHRAG_ENABLED='false',DEBUG='false',
                 KNOWALL_DATA_ROOT=str(control/'data'),KNOWALL_LOG_DIR=str(control/'logs'),
                 PYTHONPATH=str(Path(__file__).resolve().parents[1]),PYTHONIOENCODING='utf-8')
        for key in ('ANTHROPIC_AUTH_TOKEN','ANTHROPIC_BASE_URL','ANTHROPIC_MODEL','OPENAI_API_KEY','OPENAI_BASE_URL','DEEPSEEK_API_KEY'):
            env.pop(key,None)
        log_path=OUT/(mode+'-process.log');handle=log_path.open('wb');logs.append(handle)
        process=await asyncio.create_subprocess_exec(sys.executable,'tests/agent_shutdown_process_helper.py',str(control),origin,mode,
            cwd=Path(__file__).resolve().parents[1],env=env,stdout=handle,stderr=asyncio.subprocess.STDOUT)
        processes.append(process)
        return process,await wait_file(control/(mode+'-ready.json'),process)
    try:
        async with server(remote) as remote_client:
            origin=str(remote_client.base_url).rstrip('/')
            child,first=await launch('run',origin)
            async with httpx.AsyncClient(base_url=first['url'],trust_env=False,timeout=15) as client:
                headers={'Authorization':'Bearer '+first['token']}
                accepted=await client.post(PATH+'/runs',headers=headers,json={'message':'解释细胞呼吸','model':'openai'})
                assert accepted.status_code==202,accepted.text
                completed_id=accepted.json()['run_id']
                async with asyncio.timeout(6):
                    while True:
                        completed=(await client.get(PATH+'/runs/'+completed_id,headers=headers)).json()
                        if completed['status']=='completed':break
                        await asyncio.sleep(.02)
                held=await client.post(PATH+'/runs',headers=headers,json={'message':'等待清理','model':'openai'})
                assert held.status_code==202,held.text
                held_id=held.json()['run_id'];await wait_file(control/'tool-entered.json',child)
                shutting=await client.post('/__test_shutdown');assert shutting.status_code==200
                await asyncio.wait_for(cleanup_entered.wait(),6)
                # Separate DB reader proves terminal is still running while the
                # actual tool finally is held by a parent-owned local provider.
                import sqlite3
                with sqlite3.connect(database_file) as sql:
                    during=sql.execute('SELECT status FROM agent_runs WHERE id=?',(held_id,)).fetchone()[0]
                assert during=='running' and child.returncode is None
                cleanup_release.set();await asyncio.wait_for(child.wait(),12)
                assert child.returncode==0
                closed=json.loads((control/'run-closed.json').read_text(encoding='utf8'))
                assert closed['production_lifespan_exited'] and closed['blocked_external_requests']==[]
            second_child,second=await launch('read',origin)
            assert first['pid']!=second['pid'] and first['database_url']==second['database_url']
            assert first['revision']==second['revision']=='202610112250'
            async with httpx.AsyncClient(base_url=second['url'],trust_env=False,timeout=15) as reader:
                headers={'Authorization':'Bearer '+second['token']}
                observed=await reader.get(PATH+'/runs/'+held_id,headers=headers)
                preserved=await reader.get(PATH+'/runs/'+completed_id,headers=headers)
                result=await reader.get(PATH+'/runs/'+completed_id+'/result',headers=headers)
                assert observed.status_code==preserved.status_code==result.status_code==200
                assert observed.json()['status']=='cancelled' and observed.json()['cancel_requested'] is False
                assert observed.json()['steps'][-1]['type']=='run_cancelled'
                assert preserved.json()==completed and result.json()['message']=='细胞呼吸消耗氧气 [S1]'
                assert len(calls)==3
                await reader.post('/__test_shutdown');await asyncio.wait_for(second_child.wait(),12);assert second_child.returncode==0
            record(request,first_process=first,second_process=second,closed=closed,readback=observed.json(),completed=preserved.json(),
                   result=result.json(),during_tool_finally=during,provider_calls=len(calls),same_database=True,
                   startup_scope='actual product startup/init_db/main lifespan; GraphRAG disabled; temporary SQLite; no .env or paid model',
                   database_pragma_scope='Existing product connect listener retained; no test WAL/PRAGMA mutation')
    finally:
        cleanup_release.set()
        for process in processes:
            if process.returncode is None:process.kill();await process.wait()
        for handle in logs:handle.close()
