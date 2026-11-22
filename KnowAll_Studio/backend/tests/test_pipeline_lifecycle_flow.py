"""Temporary DB/accounts and real pipeline/agents; external generation is mocked."""
import asyncio
import json
import os
import socket
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.api import pipeline as api, agents
from app.core.agents.base import AgentRegistry, BaseAgent, AgentResult
from app.core.agents.orchestrator import orchestrator
from app.core.agents.study_plan_agent import StudyPlanAgent
from app.core.pipeline import pipeline, PipelineStage
from app.database import Base
from app.models import DocumentChunk, StudyPlan, KnowledgeTree
from sqlalchemy import select, func
from tests.test_study_notification_flow import flow


@pytest.fixture
async def lifecycle(flow, monkeypatch):
    from app.core.knowledge import knowledge_generator
    from app.core.knowledge.summary_generator import SummaryGenerator
    from app.core.quiz import quiz_generator
    from app.core.memory import card_generator
    import app.core.glossary.extractor as glossary
    async with flow.factory() as db:
        await db.run_sync(lambda session: Base.metadata.create_all(session.connection()))
        db.add(DocumentChunk(doc_id='source',chunk_index=0,text_content='细胞与能量',content_hash='test'))
        await db.commit()
    async def summary(*args,**kwargs):
        return {'content_md':'# 细胞\n## 能量','node_count':1,'nodes':[{
            'id':str(uuid.uuid4()),'level':2,'sequence':0,'title':'能量',
        }]}
    tree=AsyncMock(return_value={'name':'模拟树','children':[]})
    monkeypatch.setattr(knowledge_generator,'generate_tree',tree)
    monkeypatch.setattr(knowledge_generator,'generate_outline',AsyncMock(return_value='# 模拟提纲'))
    monkeypatch.setattr(SummaryGenerator,'generate',summary)
    monkeypatch.setattr(glossary,'sync_glossary_from_db',AsyncMock(return_value={'simulated':True}))
    quiz=AsyncMock(return_value=[{'question_text':'模拟题','answer':'A'}]*5)
    cards=AsyncMock(return_value=[{'front':'模拟正面','back':'模拟背面'}]*5)
    monkeypatch.setattr(quiz_generator,'generate',quiz)
    monkeypatch.setattr(card_generator,'generate',cards)
    monkeypatch.setattr(AgentRegistry,'create_all',lambda names=None:[StudyPlanAgent()])
    monkeypatch.setattr(orchestrator,'_generate_coverage_report',AsyncMock(return_value={'simulated':True}))
    app=flow.client._transport.app
    app.include_router(api.router)
    app.include_router(agents.router)
    api._active_pipelines.clear()
    yield SimpleNamespace(**vars(flow),app=app,tree=tree,quiz=quiz,cards=cards)
    api._active_pipelines.clear()


def request():
    return {'document_id':'source','question_count':5,'card_count':5}


@pytest.mark.parametrize('stream',[False,True])
async def test_pipeline_generates_owned_plan_and_final_result(lifecycle,stream):
    response=await asyncio.wait_for(lifecycle.client.post('/api/v1/pipeline/run'+('/stream' if stream else ''),headers=lifecycle.alice,json=request()),10)
    assert response.status_code==200
    if stream:
        data=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert data[-1]['stage']=='done'
        result=data[-1]['result']
    else:
        result=response.json()
    assert result['tree_id'] and result['summary_id']
    assert result['question_count']==5 and result['card_count']==5
    plan_id=result['agent_results']['study_plan']['result']['plan_id']
    async with lifecycle.factory() as db:
        assert (await db.get(StudyPlan,plan_id)).user_id=='alice'
    assert (await lifecycle.client.get('/api/v1/study/plans',headers=lifecycle.bob)).json()==[]
    assert api._active_pipelines=={}


@pytest.mark.parametrize('path,method', [('run','post'),('run/stream','post'),('cancel?document_id=source','post'),('active','get')])
async def test_invalid_token_never_runs_or_reveals_active(lifecycle,path,method):
    response=await lifecycle.client.request(method,'/api/v1/pipeline/'+path,headers={'Authorization':'Bearer invalid'},**({'json':request()} if path.startswith('run') else {}))
    assert response.status_code==401
    assert lifecycle.tree.await_count==0


async def test_active_only_lists_current_account_and_cancel_is_scoped(lifecycle):
    alice,bob=asyncio.Event(),asyncio.Event()
    api._active_pipelines.update({'alice_source':alice,'bob_source':bob})
    response=await lifecycle.client.get('/api/v1/pipeline/active',headers=lifecycle.alice)
    assert response.json()=={'active_runs':['alice_source'],'count':1}
    await lifecycle.client.post('/api/v1/pipeline/cancel?document_id=source',headers=lifecycle.alice)
    assert alice.is_set() and not bob.is_set()


async def test_duplicate_stream_cannot_replace_first_run(lifecycle):
    first=asyncio.Event()
    api._active_pipelines['alice_source']=first
    response=await lifecycle.client.post('/api/v1/pipeline/run/stream',headers=lifecycle.alice,json=request())
    assert response.status_code==409
    assert api._active_pipelines['alice_source'] is first
    assert lifecycle.tree.await_count==0


@pytest.mark.parametrize('stage',[PipelineStage.PARSE,PipelineStage.KNOWLEDGE_TREE,PipelineStage.OUTLINE,PipelineStage.AGENTS,PipelineStage.QUIZ,PipelineStage.FLASHCARDS])
async def test_cancel_at_stage_boundary_never_runs_later_stages(lifecycle,stage):
    cancel=asyncio.Event()
    iterator=pipeline.run_full_chain('source',question_count=5,card_count=5,user_id='alice',cancel_event=cancel)
    try:
        async for state in iterator:
            if state.stage==stage:
                cancel.set()
                with pytest.raises(asyncio.CancelledError):
                    await anext(iterator)
                break
        else:
            pytest.fail('requested stage was not reached')
        assert lifecycle.cards.await_count==0
        if stage!=PipelineStage.FLASHCARDS:
            assert lifecycle.quiz.await_count==0
        if stage in (PipelineStage.QUIZ,PipelineStage.FLASHCARDS):
            async with lifecycle.factory() as db:
                assert await db.scalar(select(func.count()).select_from(StudyPlan).where(StudyPlan.user_id=='alice'))==1
                assert await db.scalar(select(func.count()).select_from(KnowledgeTree))==1
    finally:
        await iterator.aclose()


@pytest.mark.parametrize('close_mode',['close','cancel'])
async def test_agent_stream_reclaims_children_when_consumer_stops(lifecycle,monkeypatch,close_mode):
    started,finished,release=asyncio.Event(),asyncio.Event(),asyncio.Event()
    class Waiting(BaseAgent):
        name='waiting'
        async def run(self,*args,**kwargs):
            started.set()
            try:
                await release.wait()
                return AgentResult(agent=self.name,status='success',result={'simulated':True})
            finally:
                finished.set()
    monkeypatch.setattr(AgentRegistry,'create_all',lambda names=None:[StudyPlanAgent(),Waiting()])
    iterator=orchestrator.orchestrate_sse('summary','source',config={'user_id':'alice'})
    pending=None
    try:
        for _ in range(3):
            await anext(iterator)
        await asyncio.wait_for(started.wait(),2)
        if close_mode=='cancel':
            pending=asyncio.create_task(anext(iterator))
            await asyncio.sleep(0)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            await iterator.aclose()
        assert finished.is_set(), 'consumer stopped but child agent still running'
    finally:
        release.set()
        if pending and not pending.done():
            pending.cancel()
        await iterator.aclose()
        await asyncio.wait_for(finished.wait(),2)


async def test_core_legacy_local_default_stays_compatible(lifecycle):
    async for state in pipeline.run_full_chain('source',question_count=5,card_count=5):
        assert state.stage!=PipelineStage.ERROR,state.error
    async with lifecycle.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudyPlan).where(StudyPlan.user_id=='local_user'))==1


@pytest.mark.parametrize('stream',[False,True])
async def test_generation_failure_returns_error_and_cleans_up(lifecycle,monkeypatch,stream):
    from app.core.knowledge import knowledge_generator
    monkeypatch.setattr(knowledge_generator,'generate_tree',AsyncMock(side_effect=ValueError('受控生成失败')))
    response=await lifecycle.client.post('/api/v1/pipeline/run'+('/stream' if stream else ''),headers=lifecycle.alice,json=request())
    if stream:
        assert response.status_code==200
        data=[json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
        assert data[-1]['stage']=='error' and data[-1]['error']=='受控生成失败'
    else:
        assert response.status_code==500 and response.json()['detail']=='受控生成失败'
    assert api._active_pipelines=={}
    assert lifecycle.quiz.await_count==0 and lifecycle.cards.await_count==0
    async with lifecycle.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudyPlan))==0


@pytest.mark.parametrize('path,method', [('run','post'),('run/stream','post'),('cancel?document_id=source','post'),('active','get')])
async def test_desktop_login_mode_requires_identity(lifecycle,monkeypatch,path,method):
    from app.config import settings
    monkeypatch.setattr(settings,'desktop_require_login',True)
    response=await lifecycle.client.request(method,'/api/v1/pipeline/'+path,**({'json':request()} if path.startswith('run') else {}))
    assert response.status_code==401
    assert lifecycle.tree.await_count==0


async def test_initial_cancel_does_not_start_generation(lifecycle):
    cancel=asyncio.Event();cancel.set()
    iterator=pipeline.run_full_chain('source',cancel_event=cancel,user_id='alice')
    with pytest.raises(asyncio.CancelledError):
        await anext(iterator)
    await iterator.aclose()
    assert lifecycle.tree.await_count==0


@asynccontextmanager
async def localhost(app):
    import uvicorn
    listener=socket.socket()
    listener.bind(('127.0.0.1',0))
    server=uvicorn.Server(uvicorn.Config(app,log_level='critical',lifespan='off'))
    task=asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{listener.getsockname()[1]}',timeout=10) as client:
            yield client
    finally:
        server.should_exit=True
        try:
            await asyncio.wait_for(task,5)
        finally:
            listener.close()


@pytest.mark.parametrize('action',['cancel','disconnect'])
async def test_real_http_pipeline_stops_pending_generation_and_clears_registration(lifecycle,monkeypatch,action):
    from app.core.knowledge import knowledge_generator
    started,finished,release=asyncio.Event(),asyncio.Event(),asyncio.Event()
    async def waiting(*args,**kwargs):
        started.set()
        try:
            await release.wait()
            return {'simulated':True}
        finally:
            finished.set()
    monkeypatch.setattr(knowledge_generator,'generate_tree',waiting)
    events=[]
    try:
        async with localhost(lifecycle.app) as client:
            async with client.stream('POST','/api/v1/pipeline/run/stream',headers=lifecycle.alice,json=request()) as response:
                assert response.status_code==200
                async for line in response.aiter_lines():
                    if not line.startswith('data: '):
                        continue
                    data=json.loads(line[6:]);events.append(data)
                    if data['stage']=='knowledge_tree':
                        await asyncio.wait_for(started.wait(),2)
                        assert (await client.get('/api/v1/pipeline/active',headers=lifecycle.alice)).json()['count']==1
                        assert (await client.get('/api/v1/pipeline/active',headers=lifecycle.bob)).json()['count']==0
                        assert (await client.post('/api/v1/pipeline/cancel?document_id=source',headers=lifecycle.bob)).json()['status']=='not_found'
                        if action=='disconnect':
                            break
                        cancelled=await client.post('/api/v1/pipeline/cancel?document_id=source',headers=lifecycle.alice)
                        assert cancelled.json()['status']=='cancelled'
                if action=='cancel':
                    assert events[-1]['stage']=='cancelled'
            await asyncio.wait_for(finished.wait(),2)
            async with asyncio.timeout(2):
                while api._active_pipelines:
                    await asyncio.sleep(0.01)
            assert lifecycle.quiz.await_count==0 and lifecycle.cards.await_count==0
            async with lifecycle.factory() as db:
                assert await db.scalar(select(func.count()).select_from(KnowledgeTree))==0
        evidence_dir=Path(os.environ.get('KNOWALL_TEST_EVIDENCE_DIR', Path(__file__).resolve().parents[2]/'docs/test-round-06b2'))
        output=evidence_dir/f'real-http-{action}.json'
        output.write_text(json.dumps({'transport':'real localhost HTTP/SSE','generation':'explicitly simulated waiting model','action':action,'generation_finished':finished.is_set(),'active_count':len(api._active_pipelines),'events':events},ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        release.set()


async def test_real_http_agent_disconnect_reclaims_child(lifecycle,monkeypatch):
    started,finished,release=asyncio.Event(),asyncio.Event(),asyncio.Event()
    class Waiting(BaseAgent):
        name='waiting'
        async def run(self,*args,**kwargs):
            started.set()
            try:
                await release.wait()
                return AgentResult(agent=self.name,status='success',result={'simulated':True})
            finally:
                finished.set()
    monkeypatch.setattr(AgentRegistry,'create_all',lambda names=None:[StudyPlanAgent(),Waiting()])
    try:
        async with localhost(lifecycle.app) as client:
            async with client.stream('POST','/api/v1/agents/orchestrate/stream',headers=lifecycle.alice,json={'summary_id':'summary','document_id':'source'}) as response:
                assert response.status_code==200
                async for line in response.aiter_lines():
                    if '"plan_id"' in line:
                        await asyncio.wait_for(started.wait(),2)
                        break
            await asyncio.wait_for(finished.wait(),2)
    finally:
        release.set()


async def test_real_http_two_accounts_cancel_independently(lifecycle,monkeypatch):
    from app.core.knowledge import knowledge_generator
    started=[asyncio.Event(),asyncio.Event()]
    finished=[asyncio.Event(),asyncio.Event()]
    release=asyncio.Event()
    calls=0
    async def waiting(*args,**kwargs):
        nonlocal calls
        index=calls;calls+=1
        started[index].set()
        try:
            await release.wait()
            return {'simulated':True}
        finally:
            finished[index].set()
    monkeypatch.setattr(knowledge_generator,'generate_tree',waiting)
    async def until_tree(lines):
        async for line in lines:
            if line.startswith('data: ') and json.loads(line[6:])['stage']=='knowledge_tree':
                return
        pytest.fail('stream ended before waiting generation')
    async def assert_cancelled(lines):
        data=[]
        async for line in lines:
            if line.startswith('data: '):
                data.append(json.loads(line[6:]))
        assert data[-1]['stage']=='cancelled'
    try:
        async with localhost(lifecycle.app) as client:
            async with client.stream('POST','/api/v1/pipeline/run/stream',headers=lifecycle.alice,json=request()) as alice:
                assert alice.status_code==200
                alice_lines=alice.aiter_lines()
                await until_tree(alice_lines)
                await asyncio.wait_for(started[0].wait(),2)
                async with client.stream('POST','/api/v1/pipeline/run/stream',headers=lifecycle.bob,json=request()) as bob:
                    assert bob.status_code==200
                    bob_lines=bob.aiter_lines()
                    await until_tree(bob_lines)
                    await asyncio.wait_for(started[1].wait(),2)
                    assert (await client.post('/api/v1/pipeline/cancel?document_id=source',headers=lifecycle.alice)).json()['status']=='cancelled'
                    await assert_cancelled(alice_lines)
                    await asyncio.wait_for(finished[0].wait(),2)
                    assert not finished[1].is_set()
                    assert (await client.get('/api/v1/pipeline/active',headers=lifecycle.alice)).json()['count']==0
                    assert (await client.get('/api/v1/pipeline/active',headers=lifecycle.bob)).json()=={'active_runs':['bob_source'],'count':1}
                    assert (await client.post('/api/v1/pipeline/cancel?document_id=source',headers=lifecycle.bob)).json()['status']=='cancelled'
                    await assert_cancelled(bob_lines)
                    await asyncio.wait_for(finished[1].wait(),2)
            assert api._active_pipelines=={}
    finally:
        release.set()
