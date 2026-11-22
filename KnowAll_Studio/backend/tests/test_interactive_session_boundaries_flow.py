"""Real quiz routes/SQLite. Actor override here; real JWT covered by native round fixture."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx,pytest
from fastapi import FastAPI,Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker,AsyncSession
from app.database import Base,get_db
from app.models import QuestionBank,AnswerRecord,KnowledgePointNode,KnowledgeCoverage
from app.api import interactive_quiz as routes
from app.core.auth import get_optional_user

BASE='/api/v1/quiz/interactive'
OPTIONS=[{'label':'A','text':'甲'},{'label':'B','text':'乙'}]

@pytest.fixture
async def flow(tmp_path,monkeypatch):
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'interactive.sqlite'))
    async with engine.begin() as c:await c.run_sync(Base.metadata.create_all)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as s:
        s.add_all([QuestionBank(id=f'q{i}',question_type='single_choice',question_text=f'原生交互第{i}题',options=OPTIONS,correct_answer='A',analysis='甲为正确答案') for i in range(1,4)])
        s.add(QuestionBank(id='bad',question_type='single_choice',question_text='缺选项',options=[],correct_answer='A'))
        s.add(KnowledgePointNode(id='kp',summary_id='sum',document_id='fixture-doc',level=1,sequence=1,title='测试知识点',explanation='明确测试来源'))
        s.add_all([KnowledgeCoverage(knowledge_point_id='kp',resource_type='question',resource_id=q) for q in ['q1','q2']]);await s.commit()
    async def db():
        async with factory() as s:yield s
    async def actor(request:Request):
        uid=request.headers.get('x-test-user','A');return None if uid=='local' else SimpleNamespace(id=uid)
    monkeypatch.setattr(routes,'_sessions',{})
    monkeypatch.setattr(routes,'load_user_api_keys',AsyncMock())
    from app.core.api_scheduler import api_client
    monkeypatch.setattr(api_client,'generate',AsyncMock(side_effect=AssertionError('No external AI allowed in interactive local grade')))
    app=FastAPI();app.include_router(routes.router);app.dependency_overrides[get_db]=db;app.dependency_overrides[get_optional_user]=actor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as client:
        yield SimpleNamespace(client=client,factory=factory,engine=engine)
    await engine.dispose()

async def start(f,user='A',**params):
    r=await f.client.get(BASE+'/start',params={'summary_id':'sum',**params},headers={'x-test-user':user});assert r.status_code==200,r.text
    return r.json()
async def submit(f,sid,user='A',qid='q1',answer='A',**extra):
    data={'question_id':qid,'user_answer':answer,**extra}
    if sid is not None:data['session_id']=sid
    return await f.client.post(BASE+'/submit',json=data,headers={'x-test-user':user})
async def records(f):
    async with f.factory() as s:return list((await s.scalars(select(AnswerRecord))).all())

@pytest.mark.parametrize('user',['A','B','local'])
async def test_owner_normal_feedback_stats_and_no_answer_disclosure(flow,user):
    quiz=await start(flow,user);assert quiz['total']==2
    assert all('correct_answer' not in q and 'analysis' not in q for q in quiz['questions'])
    sid=quiz['session_id']
    a=await submit(flow,sid,user=user,time_spent_ms=50,knowledge_point_id='kp');assert a.status_code==200 and a.json()['is_correct']
    b=await submit(flow,sid,user=user,qid='q2',answer='B',time_spent_ms=75);assert b.status_code==200 and not b.json()['is_correct']
    stats=await flow.client.get(BASE+f'/session/{sid}/stats',headers={'x-test-user':user});assert stats.status_code==200
    assert stats.json()['total_answered']==2 and stats.json()['accuracy']==.5 and stats.json()['time_spent_total_ms']==125
    rows=await records(flow);assert len(rows)==2 and {r.user_id for r in rows}=={user if user!='local' else 'local_user'}

@pytest.mark.parametrize('other',['B','local'])
@pytest.mark.parametrize('action',['stats','submit'])
async def test_other_actor_cannot_read_or_change_private_session(flow,other,action):
    sid=(await start(flow))['session_id'];await submit(flow,sid)
    before=dict(routes._sessions[sid]['answers'])
    r=await flow.client.get(BASE+f'/session/{sid}/stats',headers={'x-test-user':other}) if action=='stats' else await submit(flow,sid,user=other,qid='q2')
    assert r.status_code==404,r.text
    assert routes._sessions[sid]['answers']==before and len(await records(flow))==1

@pytest.mark.parametrize('sid',['unknown',''])
async def test_unknown_supplied_session_does_not_save_answer(flow,sid):
    r=await submit(flow,sid);assert r.status_code==404,r.text;assert await records(flow)==[]

@pytest.mark.parametrize('qid',['q3','missing'])
async def test_nonmember_question_is_rejected_before_grade_or_commit(flow,qid):
    sid=(await start(flow))['session_id'];r=await submit(flow,sid,qid=qid)
    assert r.status_code==400,r.text;assert await records(flow)==[];assert routes._sessions[sid]['answers']=={}

async def test_sequential_repeat_preserves_first_answer(flow):
    sid=(await start(flow))['session_id'];a=await submit(flow,sid,answer='B');assert a.status_code==200
    b=await submit(flow,sid,answer='A');assert b.status_code==409,b.text
    rows=await records(flow);assert len(rows)==1 and rows[0].user_answer=='B';assert not routes._sessions[sid]['answers']['q1']['is_correct']

async def test_concurrent_same_question_commits_only_once(flow,monkeypatch):
    sid=(await start(flow))['session_id'];original=AsyncSession.commit
    async def slow(db):await asyncio.sleep(.04);return await original(db)
    monkeypatch.setattr(AsyncSession,'commit',slow)
    responses=await asyncio.gather(*[submit(flow,sid) for _ in range(5)])
    assert sorted(r.status_code for r in responses)==[200,409,409,409,409]
    assert len(await records(flow))==1 and len(routes._sessions[sid]['answers'])==1

async def test_concurrent_different_questions_both_commit(flow):
    sid=(await start(flow))['session_id'];res=await asyncio.gather(submit(flow,sid),submit(flow,sid,qid='q2',answer='B'))
    assert [r.status_code for r in res]==[200,200];assert len(await records(flow))==2 and len(routes._sessions[sid]['answers'])==2

async def test_commit_failure_leaves_session_retryable(flow,monkeypatch):
    sid=(await start(flow))['session_id'];original=AsyncSession.commit;calls=0
    async def fail_once(db):
        nonlocal calls
        calls+=1
        if calls==1:raise RuntimeError('explicit simulated SQL commit outage')
        return await original(db)
    monkeypatch.setattr(AsyncSession,'commit',fail_once)
    first=await submit(flow,sid);assert first.status_code==500
    assert await records(flow)==[] and routes._sessions[sid]['answers']=={}
    second=await submit(flow,sid);assert second.status_code==200;assert len(await records(flow))==1

async def test_cancel_before_commit_releases_session_for_retry(flow,monkeypatch):
    sid=(await start(flow))['session_id'];original=AsyncSession.commit;entered=asyncio.Event()
    async def hold(db):entered.set();await asyncio.Event().wait()
    monkeypatch.setattr(AsyncSession,'commit',hold)
    task=asyncio.create_task(submit(flow,sid));await asyncio.wait_for(entered.wait(),2);task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert await records(flow)==[] and routes._sessions[sid]['answers']=={}
    monkeypatch.setattr(AsyncSession,'commit',original)
    assert (await asyncio.wait_for(submit(flow,sid),2)).status_code==200

@pytest.mark.parametrize('user',['A','local'])
async def test_legacy_no_session_is_independent_practice(flow,user):
    r=await submit(flow,None,user=user);assert r.status_code==200 and r.json()['stats']=={}
    assert len(await records(flow))==1 and not routes._sessions

@pytest.mark.parametrize('count',[0,-1,101])
async def test_start_invalid_count_is_422(flow,count):
    r=await flow.client.get(BASE+'/start',params={'summary_id':'sum','count':count});assert r.status_code==422,r.text
    assert routes._sessions=={}

@pytest.mark.parametrize('count',[1,100])
async def test_start_valid_count_respected(flow,count):
    r=await start(flow,count=count);assert r['total']==min(count,2)

async def test_negative_time_is_422_and_has_no_effect(flow):
    sid=(await start(flow))['session_id'];r=await submit(flow,sid,time_spent_ms=-1)
    assert r.status_code==422,r.text;assert await records(flow)==[]

@pytest.mark.parametrize('kp',['foreign',''])
async def test_supplied_unrelated_knowledge_point_is_rejected(flow,kp):
    sid=(await start(flow))['session_id'];r=await submit(flow,sid,knowledge_point_id=kp)
    assert r.status_code==400,r.text;assert await records(flow)==[]

async def test_expired_in_memory_session_requires_restart(flow):
    sid=(await start(flow))['session_id'];routes._sessions.clear()
    assert (await submit(flow,sid)).status_code==404
    assert (await flow.client.get(BASE+f'/session/{sid}/stats')).status_code==404
    fresh=await start(flow);assert fresh['session_id']!=sid;assert (await submit(flow,fresh['session_id'])).status_code==200
