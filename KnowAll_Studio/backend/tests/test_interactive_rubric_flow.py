"""Real interactive routes + temporary SQLite; AI is forbidden by the reused fixture."""
from copy import deepcopy
from sqlalchemy.ext.asyncio import AsyncSession
import pytest
from app.models import QuestionBank
from app.api import interactive_quiz as routes
from app.core.quiz import exam_engine
from tests.test_interactive_session_boundaries_flow import flow,start,submit,records,BASE

BLANKS=[{'index':1,'answer':'甲','hint':'第一空','extension_secret':'private-blank'}, {'index':2,'answer':'乙'}]
POINTS=[{'point':'可靠性','keywords':['reliable'],'score':3}, {'point':'流控','keywords':['flow'],'score':1}]

async def configure(f,kind='fill_blank',**changes):
    async with f.factory() as db:
        q=await db.get(QuestionBank,'q1')
        q.question_type=kind;q.question_text='交互规则题：请回答问题';q.correct_answer='甲;乙' if kind=='fill_blank' else 'reliable flow'
        q.analysis='原规则解析';q.blanks=deepcopy(BLANKS) if kind=='fill_blank' else [];q.scoring_points=deepcopy(POINTS) if kind!='fill_blank' else []
        for name,value in changes.items():setattr(q,name,value)
        await db.commit()

@pytest.mark.parametrize('kind,answer,ratio,correct,detail',[
    ('fill_blank','甲;错',.5,False,'blank_details'),('fill_blank','甲;乙',1,True,'blank_details'),
    ('fill_blank','',0,False,'blank_details'),('short_answer','reliable',.75,True,'point_details'),
    ('short_answer','flow',.25,False,'point_details'),('short_answer','reliable flow',1,True,'point_details'),
    ('term_definition','reliable',.75,True,'point_details'),('term_definition','',0,False,'point_details'),
])
async def test_partial_detail_is_truthful_in_response_sql_and_owned_readback(flow,kind,answer,ratio,correct,detail):
    await configure(flow,kind);sid=(await start(flow))['session_id'];r=await submit(flow,sid,answer=answer,time_spent_ms=77)
    assert r.status_code==200,r.text;body=r.json();assert body['partial_score']==ratio and body['is_correct'] is correct
    assert len(body[detail])==2
    if detail=='point_details':assert body['earned_score']==ratio*4 and body['score_total']==4
    stats=await flow.client.get(BASE+f'/session/{sid}/stats',headers={'x-test-user':'A'})
    saved=stats.json()['questions_detail'][0]
    for key in ['partial_score',detail,'correct_answer','analysis']:assert saved[key]==body[key]
    assert saved['user_answer']==answer and saved['time_spent_ms']==77
    rows=await records(flow);assert len(rows)==1 and rows[0].is_correct is correct
    assert (await submit(flow,sid,answer='other')).status_code==409
    assert (await flow.client.get(BASE+f'/session/{sid}/stats',headers={'x-test-user':'B'})).status_code==404
    assert len(await records(flow))==1

@pytest.mark.parametrize('kind',['fill_blank','short_answer','term_definition'])
async def test_unanswered_public_response_does_not_disclose_rules_or_extensions(flow,kind):
    await configure(flow,kind);quiz=await start(flow);qid=quiz['questions'][0];sid=quiz['session_id']
    assert not {'correct_answer','analysis','blanks','scoring_points'}&qid.keys()
    private=routes._sessions[sid]['questions'][0]
    assert private['blanks']==(BLANKS if kind=='fill_blank' else [])
    assert private['scoring_points']==([] if kind=='fill_blank' else POINTS)
    assert (await flow.client.get(BASE+f'/session/{sid}/stats',headers={'x-test-user':'A'})).json()['questions_detail']==[]

@pytest.mark.parametrize('kind',['fill_blank','short_answer','term_definition'])
async def test_old_session_freezes_rules_new_session_and_standalone_follow_sql(flow,kind):
    await configure(flow,kind);old=(await start(flow))['session_id']
    changes={'correct_answer':'新规则答案','blanks':[{'index':1,'answer':'错'},{'index':2,'answer':'甲'}]} if kind=='fill_blank' else {'correct_answer':'新规则答案','scoring_points':[{'point':'新权重','keywords':['flow'],'score':3},{'point':'旧词','keywords':['reliable'],'score':1}]}
    await configure(flow,kind,**changes);new=(await start(flow))['session_id'];answer='甲;错' if kind=='fill_blank' else 'reliable'
    a=(await submit(flow,old,answer=answer)).json();b=(await submit(flow,new,answer=answer)).json();c=(await submit(flow,None,answer=answer)).json()
    assert a['partial_score']==(.5 if kind=='fill_blank' else .75)
    assert b['partial_score']==(0 if kind=='fill_blank' else .25) and c['partial_score']==b['partial_score']
    assert a['correct_answer']!='新规则答案' and b['correct_answer']==c['correct_answer']=='新规则答案'

@pytest.mark.parametrize('field,bad',[('blanks',{}),('blanks',[{'index':True,'answer':'甲'}]),('scoring_points',[{'point':'坏','keywords':'secret','score':3}]),('scoring_points',[{'point':'坏','keywords':['x'],'score':-1}])])
async def test_invalid_saved_rubric_excluded_and_standalone_rejected_without_records(flow,field,bad):
    await configure(flow,**{field:bad});quiz=await start(flow);assert all(q['id']!='q1' for q in quiz['questions'])
    r=await submit(flow,None,answer='甲;乙');assert r.status_code==409,r.text;assert await records(flow)==[]

async def test_rubric_commit_failure_leaves_retry_and_snapshot_consistent(flow,monkeypatch):
    await configure(flow);sid=(await start(flow))['session_id'];original=AsyncSession.commit
    async def fail(db):raise RuntimeError('explicit test-only commit failure')
    monkeypatch.setattr(AsyncSession,'commit',fail);r=await submit(flow,sid,answer='甲;错');assert r.status_code==500
    assert await records(flow)==[] and routes._sessions[sid]['answers']=={}
    monkeypatch.setattr(AsyncSession,'commit',original);r=await submit(flow,sid,answer='甲;错');assert r.status_code==200 and r.json()['partial_score']==.5
    assert len(await records(flow))==1

def test_local_engine_total_uses_rubric_ratio_and_keeps_legacy_binary():
    result=exam_engine.grade({'questions':[{'id':'q','question_text':'多空题','question_type':'fill_blank','answer':'甲;乙','blanks':BLANKS},{'id':'legacy','question_text':'合法旧单选题','question_type':'single_choice','answer':'A','options':[{'label':'A','text':'甲'},{'label':'B','text':'乙'}]}]}, {'q':'甲;错','legacy':'A'})
    assert result['correct']==1 and result['score']==7.5 and result['percentage']==75
    assert 'partial_score' not in result['details'][1]

@pytest.mark.parametrize('weights',[[.66,.4],[.3,.1],[0,0],[.04,.02]])
@pytest.mark.parametrize('enhanced',[False,True])
async def test_fractional_rubric_full_credit_is_bounded_and_zero_weights_safe(weights,enhanced):
    points=[{'point':'first','keywords':['reliable'],'score':weights[0]},{'point':'second','keywords':['flow'],'score':weights[1]}]
    paper={'questions':[{'id':'q','question_type':'short_answer','question_text':'加权题','answer':'reliable flow','scoring_points':points}]}
    result=await exam_engine.grade_enhanced(paper,{'q':'reliable flow'},enable_semantic=False) if enhanced else exam_engine.grade(paper,{'q':'reliable flow'})
    expected=1 if sum(weights)>0 else 0
    assert result['details'][0]['partial_score']==pytest.approx(expected)
    assert result['score']==pytest.approx(expected*5)
    assert result['details'][0]['earned_score']<=sum(weights)
