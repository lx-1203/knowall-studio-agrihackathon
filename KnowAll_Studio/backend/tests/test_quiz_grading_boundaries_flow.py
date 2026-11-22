"""Actual grading/SQLite routes; only external semantic AI is explicitly mocked."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.core.quiz import ExamEngine
from app.core.learning_workbench import question_quality,grade_interaction
from app.api import questions
from app.database import Base,get_db
from app.core.auth import get_optional_user
from app.models import QuestionBank,ExamPaper,AnswerRecord,ErrorLog

OPTIONS=[{'label':c,'text':'选项'+c} for c in 'ABC']
CASES=[
 ('single_choice','A',' a ',True),('single_choice','A','B',False),('single_choice','A','',False),
 ('multi_choice','A,C','C,A',True),('multi_choice','AC','A',False),('multi_choice','AC','A,B,C',False),
 ('multi_choice','AC','A,A,C',False),('multi_choice',['A','C'],'C,A',True),
 ('multi_choice','["A", "C"]','A,C',True),('multi_choice',"['A', 'C']",'A,C',True),
 ('true_false','true','正确',True),('true_false','false','0',True),('true_false','false','true',False),
 ('fill_blank','氢','氢',True),('fill_blank','0','0',True),('fill_blank','TCP','tcp',True),('fill_blank','网络','',False),
 ('calculation','2','2.005',True),('calculation','2','2.1',False),('calculation','2','NaN',False),
 ('formula','E=mc^2',' E = mc^2 ',True),('coding','return a + b','return a+b',True),
 ('short_answer','reliable transfer','reliable transfer',True),('material_analysis','network layer','network layer',True),
 ('term_definition','传输层','传输层',True),
]
@pytest.mark.parametrize('enhanced',[False,True])
@pytest.mark.parametrize('kind,answer,user,correct',CASES)
async def test_real_engine_type_matrix(kind,answer,user,correct,enhanced):
    q={'id':'q','question_type':kind,'question_text':'实际评分题目','answer':answer,'options':OPTIONS if 'choice' in kind else []}
    engine=ExamEngine(); paper={'questions':[q]}
    result=await engine.grade_enhanced(paper,{'q':user},enable_semantic=False) if enhanced else engine.grade(paper,{'q':user})
    assert result['details'][0]['is_correct'] is correct
    assert result['correct']==int(correct) and result['score']==int(correct)*5

@pytest.mark.parametrize('answer,user,expected',[('A','正确',True),('B','错误',True),('A','B',False)])
@pytest.mark.parametrize('reverse',[False,True])
async def test_prompt_boolean_option_labels_are_eligible_and_respect_option_meaning(answer,user,expected,reverse):
    options=[{'label':'A','text':'正确'},{'label':'B','text':'错误'}]
    if reverse: options.reverse(); options[0]['label']='A'; options[1]['label']='B'
    q={'id':'q','question_type':'true_false','question_text':'判断以下说法是否正确','answer':answer,'options':options}
    assert question_quality(q)['eligible']
    expected_value=(not expected) if reverse and user in {'正确','错误'} else expected
    assert grade_interaction(q,user)['is_correct'] is expected_value
    engine=ExamEngine()
    assert engine.grade({'questions':[q]},{'q':user})['details'][0]['is_correct'] is expected_value
    assert (await engine.grade_enhanced({'questions':[q]},{'q':user},enable_semantic=False))['details'][0]['is_correct'] is expected_value

@pytest.mark.parametrize('user,parts,ratio',[
 (';网络;传输',['','网络','传输'],2/3),('网络;;传输',['网络','','传输'],2/3),
 ('网络;网络;',['网络','网络',''],2/3),(';;',['','',''],0),
 ('网络；网络；传输',['网络','网络','传输'],1),
])
def test_blank_position_is_preserved_including_unanswered_slots(user,parts,ratio):
    blanks=[{'index':i+1,'answer':v} for i,v in enumerate(['网络','网络','传输'])]
    result=ExamEngine()._score_multi_blank(user,blanks)
    assert [d['user_answer'] for d in result['blank_details']]==parts
    assert result['score_ratio']==pytest.approx(ratio)

@pytest.mark.parametrize('payload',[{}, {'passed':'false','total_score':1}, {'passed':True,'total_score':'10'},
 {'passed':True,'total_score':float('nan')},{'passed':True,'total_score':float('inf')},
 {'passed':True,'total_score':11},{'passed':False,'total_score':-1},{'passed':True,'total_score':True}])
async def test_invalid_simulated_semantic_schema_falls_back_to_actual_local_grade(monkeypatch,payload):
    from app.core.api_scheduler import api_client
    mock=AsyncMock(return_value=SimpleNamespace(content=json.dumps(payload)))
    monkeypatch.setattr(api_client,'generate',mock)
    q={'id':'q','question_type':'short_answer','question_text':'解释可靠传输','answer':'reliable transfer'}
    r=await ExamEngine().grade_enhanced({'questions':[q]},{'q':'unrelated wrong statement'})
    assert mock.await_count==1
    assert r['details'][0]['is_correct'] is False
    assert r['details'][0]['grading_method']=='local' and r['semantic_graded_count']==0

async def test_valid_simulated_semantic_result_and_provider_failure(monkeypatch):
    from app.core.api_scheduler import api_client
    q={'id':'q','question_type':'short_answer','question_text':'解释可靠传输','answer':'reliable transfer'}
    mock=AsyncMock(return_value=SimpleNamespace(content=json.dumps({'total_score':8,'passed':True,'scores':{'correctness':8},'feedback':{'suggestion':'明确模拟'}})))
    monkeypatch.setattr(api_client,'generate',mock)
    first=await ExamEngine().grade_enhanced({'questions':[q]},{'q':'a complete answer'})
    assert first['correct']==1 and first['semantic_graded_count']==1
    mock.side_effect=RuntimeError('explicit simulated AI outage')
    second=await ExamEngine().grade_enhanced({'questions':[q]},{'q':'reliable transfer'})
    assert second['correct']==1 and second['semantic_graded_count']==0

@pytest.mark.parametrize('field,value',[('feedback',{'strengths':'not a list'}),('feedback','not an object'),
 ('scores',[]),('scores',{'correctness':float('nan')}),('key_points_matched','not a list')])
async def test_invalid_semantic_feedback_cannot_be_delivered_to_result_ui(monkeypatch,field,value):
    from app.core.api_scheduler import api_client
    monkeypatch.setattr(api_client,'generate',AsyncMock(return_value=SimpleNamespace(content=json.dumps({'total_score':8,'passed':True,field:value}))))
    q={'id':'q','question_type':'short_answer','question_text':'解释可靠传输','answer':'reliable transfer'}
    r=await ExamEngine().grade_enhanced({'questions':[q]},{'q':'unrelated wrong statement'})
    assert r['correct']==0 and r['semantic_graded_count']==0
    assert r['details'][0]['grading_method']=='local'

@pytest.fixture
async def flow(tmp_path):
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'quiz.sqlite'))
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async def db():
        async with factory() as s: yield s
    app=FastAPI(); app.include_router(questions.router); app.dependency_overrides[get_db]=db
    app.dependency_overrides[get_optional_user]=lambda: SimpleNamespace(id='A')
    async with factory() as s:
        s.add_all([QuestionBank(id='q1',question_type='single_choice',question_text='选择第一题',options=OPTIONS,correct_answer='A'),QuestionBank(id='q2',question_type='multi_choice',question_text='选择第二题',options=OPTIONS,correct_answer='AC')])
        s.add(ExamPaper(id='paper',title='多题试卷',question_ids=['q1','q2'])); await s.commit()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as client: yield client,factory,app
    finally: await engine.dispose()

async def test_multi_question_second_submission_is_clear_400_and_preserves_records(flow):
    client,factory,app=flow; data={'paper_id':'paper','answers':{'q1':'B','q2':'A,C'},'enable_semantic':False}
    first=await client.post('/api/v1/quiz/exam/submit',json=data)
    assert first.status_code==200 and first.json()['score']==5
    second=await client.post('/api/v1/quiz/exam/submit',json=data)
    assert second.status_code==400,second.text
    assert '不能重复提交' in second.json()['detail']
    async with factory() as s:
        assert await s.scalar(select(func.count()).select_from(AnswerRecord))==2
        assert await s.scalar(select(func.count()).select_from(ErrorLog))==1
    app.dependency_overrides[get_optional_user]=lambda: SimpleNamespace(id='B')
    third=await client.post('/api/v1/quiz/exam/submit',json=data)
    assert third.status_code==200
    async with factory() as s:
        assert await s.scalar(select(func.count()).select_from(AnswerRecord).where(AnswerRecord.user_id=='B'))==2
