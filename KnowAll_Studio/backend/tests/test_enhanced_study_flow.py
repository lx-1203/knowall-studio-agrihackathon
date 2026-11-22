"""Isolated enhanced plans and real agent orchestration; coverage/AI are simulated."""
import json
import re
import asyncio
import socket
import os
from pathlib import Path
from datetime import datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, func, delete

from tests.test_study_notification_flow import flow
from app.api import agents
from app.core.agents.study_plan_agent import StudyPlanAgent
from app.core.agents.orchestrator import orchestrator
from app.core.agents.base import BaseAgent, AgentRegistry, AgentResult
from app.models import StudyPlan, KnowledgeSummary, KnowledgePointNode


@pytest.fixture
async def enhanced(flow, monkeypatch):
    flow.client._transport.app.include_router(agents.router)
    monkeypatch.setattr(orchestrator, '_generate_coverage_report', AsyncMock(return_value={'simulated': True}))
    async with flow.factory() as db:
        for i in range(1,17):
            db.add(KnowledgePointNode(id=f'point-{i}',summary_id='summary',document_id='source',level=2,sequence=i,title=f'细胞{i}',explanation='本地知识点'))
        await db.commit()
    return flow


async def generate(flow, **fields):
    return await flow.client.post('/api/v1/study/generate-plan-enhanced',headers=flow.alice,json={'summary_id':'summary','start_date':'2026-10-10',**fields})


@pytest.mark.parametrize('fields',[
    {'plan_type':'invalid'}, {'daily_hours':0}, {'daily_hours':-1},
    {'daily_hours':17}, {'daily_hours':'broken'}, {'daily_hours':True},
    {'daily_hours':0.001}, {'start_date':'not-date'}, {'summary_id':' '},
])
async def test_invalid_enhanced_input_never_saves_a_plan(enhanced,fields):
    response=await generate(enhanced,**fields)
    assert response.status_code==422, response.text
    async with enhanced.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudyPlan))==0


@pytest.mark.parametrize('enabled',[False,True])
async def test_review_switch_controls_returned_and_saved_nodes(enhanced,enabled):
    response=await generate(enhanced,plan_type='both',ebbinghaus_enabled=enabled)
    assert response.status_code==200, response.text
    result=response.json()['result']
    assert bool(result['ebbinghaus_nodes'])==enabled
    days=[day for week in result['long_term_plan'] for day in week['days']]
    assert any(day['is_review_day'] for day in days)==enabled
    async with enhanced.factory() as db:
        saved=await db.get(StudyPlan,result['plan_id'])
        assert bool(saved.ebbinghaus_nodes)==enabled and saved.user_id=='alice'


async def test_enhanced_plan_metadata_is_readable_after_generation(enhanced):
    response=await generate(enhanced,plan_type='short',daily_hours=1.5)
    result=response.json()['result']
    detail=await enhanced.client.get('/api/v1/study/plans/'+result['plan_id'],headers=enhanced.alice)
    assert detail.status_code==200
    data=detail.json()
    assert data.get('plan_type')=='short' and data.get('daily_hours')==1.5
    assert data.get('knowledge_point_ids')==result['knowledge_point_ids']


async def test_empty_summary_is_a_failed_generation_with_no_record(enhanced):
    async with enhanced.factory() as db:
        await db.execute(delete(KnowledgePointNode))
        (await db.get(KnowledgeSummary,'summary')).content_md='   '
        await db.commit()
    response=await generate(enhanced)
    assert response.status_code==500, response.text
    async with enhanced.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudyPlan))==0


async def test_missing_summary_is_not_generated(enhanced):
    assert (await generate(enhanced,summary_id='missing')).status_code==404


def topic_numbers(label):
    match=re.search(r'知识点\s*(\d+)(?:\s*-\s*(\d+))?',label)
    assert match, label
    return list(range(int(match[1]),int(match[2] or match[1])+1))


@pytest.mark.parametrize('total',[1,5,16,31,60,101])
def test_short_schedule_covers_every_topic_exactly_once(total):
    schedule=StudyPlanAgent()._build_short_term_plan(total,2.5,datetime(2026,10,10))
    numbers=[number for day in schedule for slot in day['hours'] if not slot['is_review'] for number in topic_numbers(slot['topic'])]
    assert numbers==list(range(1,total+1)), numbers


@pytest.mark.parametrize('hours',[0.5,1.5,2.5,16])
def test_fractional_time_slots_preserve_minutes_and_stay_in_the_day(hours):
    schedule=StudyPlanAgent()._build_short_term_plan(16,hours,datetime(2026,10,10))
    for day in schedule:
        minutes=0
        previous=8*60
        for slot in day['hours']:
            start,end=slot['hour'].split('-')
            start_min,end_min=[sum(int(v)*factor for v,factor in zip(text.split(':'),(60,1))) for text in (start,end)]
            assert start_min==previous and start_min<end_min<=24*60
            minutes+=end_min-start_min;previous=end_min
        assert minutes==round(hours*60)


@pytest.mark.parametrize('total',[1,5,14,15,31,57,120])
def test_long_schedule_covers_every_topic_exactly_once(total):
    schedule=StudyPlanAgent()._build_long_term_plan(total,2,datetime(2026,10,10),[])
    numbers=[number for week in schedule for day in week['days'] for topic in day['topics'] for number in topic_numbers(topic)]
    assert numbers==list(range(1,total+1)), numbers


@pytest.mark.parametrize('stream',[False,True])
async def test_summary_orchestration_uses_server_identity_over_client_config(enhanced,stream):
    response=await enhanced.client.post('/api/v1/agents/orchestrate'+('/stream' if stream else ''),headers=enhanced.alice,json={
        'summary_id':'summary','document_id':'source','agents':['study_plan'],
        'config':{'user_id':'bob','study_plan':{'type':'short','daily_hours':1.5}},
    })
    assert response.status_code==200, response.text
    if stream:
        blocks=[block for block in response.text.strip().split('\n\n') if block]
        events=[]
        for block in blocks:
            event=next(line[7:] for line in block.splitlines() if line.startswith('event: '))
            data=json.loads(next(line[6:] for line in block.splitlines() if line.startswith('data: ')))
            events.append((event,data))
        assert events[-1][0]=='orchestrate_complete'
        assert events[-1][1]['results']['study_plan']['status']=='success'
        result=events[-1][1]['results']['study_plan']['result']
    else:
        assert response.json()['results']['study_plan']['status']=='success'
        result=response.json()['results']['study_plan']['result']
    assert result['plan_type']=='short' and result['daily_hours']==1.5
    async with enhanced.factory() as db:
        plans=(await db.execute(select(StudyPlan))).scalars().all()
        assert len(plans)==1 and plans[0].user_id=='alice'
    assert (await enhanced.client.get('/api/v1/study/plans',headers=enhanced.bob)).json()==[]


@pytest.mark.parametrize('stream',[False,True])
async def test_orchestration_rejects_invalid_presented_token(enhanced,stream):
    response=await enhanced.client.post('/api/v1/agents/orchestrate'+('/stream' if stream else ''),headers={'Authorization':'Bearer invalid'},json={
        'summary_id':'summary','document_id':'source','agents':['study_plan'],
    })
    assert response.status_code==401, response.text


async def test_stream_rejects_missing_source_before_running_agents(enhanced,monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(StudyPlanAgent,'run',run)
    response=await enhanced.client.post('/api/v1/agents/orchestrate/stream',headers=enhanced.alice,json={
        'summary_id':'missing','document_id':'source','agents':['study_plan'],
    })
    assert response.status_code==404
    run.assert_not_awaited()


async def test_stream_reports_agent_failure_as_json_error(enhanced,monkeypatch):
    run = AsyncMock(return_value=AgentResult(agent='study_plan',status='error',error='SIMULATED scheduler failure'))
    monkeypatch.setattr(StudyPlanAgent,'run',run)
    response=await enhanced.client.post('/api/v1/agents/orchestrate/stream',headers=enhanced.alice,json={
        'summary_id':'summary','document_id':'source','agents':['study_plan'],
    })
    assert response.status_code==200
    events=[]
    for block in response.text.strip().split('\n\n'):
        lines=block.splitlines()
        events.append((lines[0][7:],json.loads(lines[1][6:])))
    failure=next(data for event,data in events if event=='agent_error')
    assert failure['agent']=='study_plan' and failure['error']=='SIMULATED scheduler failure'
    assert events[-1][1]['results']['study_plan']['status']=='error'
    run.assert_awaited_once()


async def test_short_time_slots_reference_real_knowledge_ids(enhanced):
    result=(await generate(enhanced,plan_type='short',daily_hours=2.5)).json()['result']
    ids=[point for day in result['short_term_plan'] for slot in day['hours'] for point in slot['knowledge_point_ids']]
    assert ids==[f'point-{i}' for i in range(1,17)]


async def test_direct_agent_flattened_config_is_validated_before_saving(enhanced):
    result=await StudyPlanAgent().run('summary','source',study_plan={'type':'wrong'},user_id='alice')
    assert result.status=='error'
    async with enhanced.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudyPlan))==0


@pytest.mark.parametrize('all_skipped',[False,True])
async def test_stream_keeps_skipped_agents_in_final_result(enhanced,monkeypatch,all_skipped):
    class Quiet(BaseAgent):
        name='quiet'
        async def run(self,*args,**kwargs):
            raise AssertionError('skipped agent must not run')
        def should_run(self,*args,**kwargs):
            return False
    selected=[Quiet()] if all_skipped else [Quiet(),StudyPlanAgent()]
    monkeypatch.setattr(AgentRegistry,'create_all',lambda names=None:selected)
    response=await enhanced.client.post('/api/v1/agents/orchestrate/stream',headers=enhanced.alice,json={'summary_id':'summary','document_id':'source'})
    final=json.loads(response.text.strip().split('\n\n')[-1].split('\ndata: ')[1])
    assert final['results']['quiet']['status']=='skipped'
    if not all_skipped:
        assert final['results']['study_plan']['status']=='success'


async def test_stream_escapes_error_text_and_preserves_other_success(enhanced,monkeypatch):
    class Broken(BaseAgent):
        name='broken'
        async def run(self,*args,**kwargs):
            raise ValueError('坏"内容\n第二行')
    monkeypatch.setattr(AgentRegistry,'create_all',lambda names=None:[Broken(),StudyPlanAgent()])
    response=await enhanced.client.post('/api/v1/agents/orchestrate/stream',headers=enhanced.alice,json={'summary_id':'summary','document_id':'source'})
    parsed=[(block.splitlines()[0][7:],json.loads(block.splitlines()[1][6:])) for block in response.text.strip().split('\n\n')]
    error=next(data for event,data in parsed if event=='agent_error')
    assert error['error']=='坏"内容\n第二行'
    assert parsed[-1][1]['results']['broken']['status']=='error'
    assert parsed[-1][1]['results']['study_plan']['status']=='success'


@pytest.mark.parametrize('hours',[float('inf'),float('nan')])
async def test_nonfinite_pipeline_hours_do_not_persist(enhanced,hours):
    result=await StudyPlanAgent().run('summary','source',study_plan={'daily_hours':hours},user_id='alice')
    assert result.status=='error'
    async with enhanced.factory() as db:
        assert await db.scalar(select(func.count()).select_from(StudyPlan))==0


async def test_offset_start_date_is_stored_as_utc(enhanced):
    response=await generate(enhanced,plan_type='short',start_date='2026-10-10T08:00:00+08:00')
    assert response.status_code==200
    data=response.json()['result']
    assert data['short_term_plan'][0]['date']=='2026-10-10'
    async with enhanced.factory() as db:
        # The deadline is the inclusive final scheduled date, with the UTC
        # start normalized before generating calendar slots.
        assert (await db.get(StudyPlan,data['plan_id'])).target_end_date==datetime(2026,10,10)


async def test_real_http_stream_delivers_plan_before_other_agent_finishes(enhanced,monkeypatch,tmp_path):
    import httpx
    import uvicorn
    release,finished=asyncio.Event(),asyncio.Event()
    class Waiting(BaseAgent):
        name='waiting'
        async def run(self,*args,**kwargs):
            await release.wait()
            finished.set()
            return AgentResult(agent=self.name,status='success',result={'simulated':True})
    monkeypatch.setattr(AgentRegistry,'create_all',lambda names=None:[StudyPlanAgent(),Waiting()])
    listener=socket.socket()
    listener.bind(('127.0.0.1',0))
    port=listener.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(enhanced.client._transport.app,log_level='critical',lifespan='off'))
    task=asyncio.create_task(server.serve(sockets=[listener]))
    observed=[]
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',timeout=5) as client:
            async with client.stream('POST','/api/v1/agents/orchestrate/stream',headers=enhanced.alice,json={
                'summary_id':'summary','document_id':'source',
                'config':{'study_plan':{'type':'short'},'start_date':'2026-10-10'},
            }) as response:
                assert response.status_code==200
                assert response.headers['content-type'].startswith('text/event-stream')
                block=[]
                async for line in response.aiter_lines():
                    if line:
                        block.append(line);continue
                    if not block:
                        continue
                    event=block[0][7:]
                    data=json.loads(block[1][6:])
                    observed.append({'event':event,'data':data})
                    block=[]
                    if event=='agent_complete' and data['agent']=='study_plan':
                        assert not finished.is_set()
                        async with enhanced.factory() as db:
                            assert (await db.get(StudyPlan,data['result']['plan_id'])).user_id=='alice'
                        release.set()
                assert observed[-1]['event']=='orchestrate_complete' and finished.is_set()
                assert observed[-1]['data']['results']['study_plan']['status']=='success'
                assert observed[-1]['data']['results']['waiting']['status']=='success'
        output_dir=Path(os.environ.get('KNOWALL_ENHANCED_EVIDENCE_DIR',str(tmp_path)))
        output_dir.mkdir(parents=True,exist_ok=True)
        output=output_dir/'real-http-sample.json'
        output.write_text(json.dumps({'transport':'real localhost HTTP/SSE','external_coverage':'simulated','waiting_agent':'simulated','events':observed},ensure_ascii=False,indent=2),encoding='utf-8')
    finally:
        release.set()
        server.should_exit=True
        await asyncio.wait_for(task,5)
        listener.close()
