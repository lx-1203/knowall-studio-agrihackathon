"""Real quiz routers/isolated SQLite; generation is explicitly simulated."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx,pytest
from fastapi import FastAPI
from sqlalchemy import select,func,create_engine,text
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from alembic import command
from alembic.config import Config
from app.database import Base,get_db
from app.models import QuestionBank
from app.api import questions as api
from app.core.auth import get_optional_user
from app.core.quiz import exam_engine
BLANKS=[{'index':1,'answer':'氢','hint':'第一元素'},{'index':2,'answer':'氧','hint':'第二元素'}]
POINTS=[{'point':'可靠传输','keywords':['reliable'],'score':3},{'point':'流量控制','keywords':['flow'],'score':1}]
def item(kind='fill_blank'):
 return {'question_type':kind,'question_text':'元数据测试填空___和___' if kind=='fill_blank' else '元数据测试解释传输','answer':'完整参考答案','blanks':BLANKS if kind=='fill_blank' else [],'scoring_points':POINTS if kind!='fill_blank' else []}

@pytest.fixture
async def flow(tmp_path,monkeypatch):
 engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'rubric.sqlite'));factory=async_sessionmaker(engine,expire_on_commit=False)
 async with engine.begin() as c:await c.run_sync(Base.metadata.create_all)
 async def db():
  async with factory() as s:yield s
 app=FastAPI();app.include_router(api.router);app.dependency_overrides[get_db]=db;app.dependency_overrides[get_optional_user]=lambda:SimpleNamespace(id='fixture-A')
 monkeypatch.setattr(api,'load_user_api_keys',AsyncMock());monkeypatch.setattr(api,'load_document_chunks',AsyncMock(return_value=[{'id':'source','text_content':'明确模拟资料'}]))
 generated=AsyncMock(return_value=([item()],{},[]));monkeypatch.setattr(api,'generate_section_questions',generated)
 monkeypatch.setattr(exam_engine,'grade_semantic',AsyncMock(side_effect=AssertionError('External AI forbidden in rubric flow')))
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as client:yield SimpleNamespace(client=client,factory=factory,engine=engine,generated=generated)
 await engine.dispose()
async def save(f,rows):return await f.client.post('/api/v1/quiz/bank/save',json={'questions':rows})
async def create(f,ids=None):return await f.client.post('/api/v1/quiz/exam/create',json={'question_ids':ids} if ids else {})

@pytest.mark.parametrize('preview',[True,False])
@pytest.mark.parametrize('kind',['fill_blank','short_answer'])
async def test_generated_preview_and_autosave_retain_structured_metadata(flow,preview,kind):
 flow.generated.return_value=([item(kind)],{},[])
 r=await flow.client.post('/api/v1/quiz/generate',json={'document_id':'d','preview':preview});assert r.status_code==200,r.text
 q=r.json()['questions'][0];assert q['blanks']==item(kind)['blanks'];assert q['scoring_points']==item(kind)['scoring_points']
 if not preview:
  async with flow.factory() as db:stored=await db.get(QuestionBank,q['id']);assert stored.blanks==item(kind)['blanks'];assert stored.scoring_points==item(kind)['scoring_points']

@pytest.mark.parametrize('kind',['fill_blank','short_answer','term_definition'])
@pytest.mark.parametrize('specified',[True,False])
async def test_bank_commit_new_session_and_both_exam_assembly_paths(flow,kind,specified):
 r=await save(flow,[item(kind)]);assert r.status_code==200,r.text;ids=r.json()['question_ids']
 async with flow.factory() as db:
  stored=await db.get(QuestionBank,ids[0]);assert getattr(stored,'blanks',None)==item(kind)['blanks'];assert getattr(stored,'scoring_points',None)==item(kind)['scoring_points']
 r=await create(flow,ids if specified else None);assert r.status_code==200,r.text
 q=r.json()['questions'][0];assert q['blanks']==item(kind)['blanks'];assert q['scoring_points']==item(kind)['scoring_points']

@pytest.mark.parametrize('kind,user,ratio',[('fill_blank','氢;错误答案一',.5),('fill_blank','氧;氢',0),('short_answer','reliable',.75),('term_definition','reliable',.75)])
async def test_actual_saved_exam_local_partial_credit(flow,kind,user,ratio):
 r=await save(flow,[item(kind)]);assert r.status_code==200,r.text;ids=r.json()['question_ids'];paper=(await create(flow,ids)).json()
 r=await flow.client.post('/api/v1/quiz/exam/submit',json={'paper_id':paper['paper_id'],'answers':{ids[0]:user},'enable_semantic':False});assert r.status_code==200,r.text
 assert r.json()['details'][0]['partial_score']==ratio

@pytest.mark.parametrize('kind,user,ratio',[('fill_blank','氢;错误答案一',.5),('short_answer','reliable transfer',.75)])
async def test_deterministic_rubric_not_overridden_by_semantic_setting(flow,kind,user,ratio):
 q={'id':'q',**item(kind)};result=await exam_engine.grade_enhanced({'questions':[q]},{'q':user},enable_semantic=True)
 exam_engine.grade_semantic.assert_not_awaited();assert result['details'][0]['partial_score']==ratio

@pytest.mark.parametrize('field,bad',[('blanks',{}),('blanks',[None]),('blanks',[{'index':1,'answer':None}]),('blanks',[{'index':True,'answer':'氢'}]),('scoring_points',[{'point':'x','keywords':[5],'score':1}]),('scoring_points',[{'point':'x','keywords':['x'],'score':-1}]),('scoring_points','invalid')])
async def test_invalid_metadata_rejected_before_any_batch_commit(flow,field,bad):
 q=item();q[field]=bad;r=await save(flow,[item(),q]);assert r.status_code==422,r.text
 async with flow.factory() as db:assert await db.scalar(select(func.count()).select_from(QuestionBank))==0

async def test_old_questions_without_or_with_null_metadata_keep_legacy_behavior(flow):
 q={'question_type':'fill_blank','question_text':'旧单空','answer':'氢'}
 r=await save(flow,[q,{**q,'blanks':None,'scoring_points':None}]);assert r.status_code==200,r.text
 paper=(await create(flow,r.json()['question_ids'])).json();assert all(x['blanks']==[] and x['scoring_points']==[] for x in paper['questions'])

async def test_bad_generated_metadata_reports_502_without_autosave(flow):
 flow.generated.return_value=([{**item(),'blanks':[None]}],{},[])
 r=await flow.client.post('/api/v1/quiz/generate',json={'document_id':'d','preview':False});assert r.status_code==502,r.text
 async with flow.factory() as db:assert await db.scalar(select(func.count()).select_from(QuestionBank))==0

@pytest.mark.parametrize('kind,user,score,percentage', [('fill_blank','氢;错误',2.5,50),('short_answer','reliable',3.75,75),('term_definition','reliable',3.75,75)])
async def test_explicit_rubric_partial_credit_is_included_in_paper_totals(kind,user,score,percentage):
 result=await exam_engine.grade_enhanced({'questions':[{'id':'q',**item(kind)}]},{'q':user},enable_semantic=False)
 assert result['score']==score;assert result['percentage']==percentage

async def test_partial_rubric_and_legacy_question_have_consistent_mixed_total():
 result=await exam_engine.grade_enhanced({'questions':[{'id':'q',**item()}, {'id':'old','question_type':'single_choice','question_text':'旧单选','options':[{'label':'A','text':'甲'},{'label':'B','text':'乙'}],'answer':'A'}]},{'q':'氢;错误','old':'A'},enable_semantic=False)
 assert result['score']==7.5;assert result['percentage']==75;assert result['correct']==1

def test_actual_alembic_upgrade_preserves_legacy_question_and_refuses_lossy_downgrade(tmp_path):
 backend=Path(__file__).resolve().parents[1];migration=backend/'alembic/versions/202610100004_add_quiz_rubrics.py';assert migration.exists()
 engine=create_engine('sqlite:///'+str(tmp_path/'migration.sqlite'));cfg=Config(str(backend/'alembic.ini'));cfg.set_main_option('script_location',str(backend/'alembic'))
 with engine.begin() as c:
  c.execute(text('CREATE TABLE question_bank(id TEXT PRIMARY KEY, question_text TEXT, correct_answer TEXT)'));c.execute(text("INSERT INTO question_bank VALUES('old','原题','原答案')"));cfg.attributes['connection']=c;command.stamp(cfg,'202610092122');command.upgrade(cfg,'202610100004')
  assert c.execute(text("SELECT question_text,correct_answer,blanks,scoring_points FROM question_bank WHERE id='old'")).one()==('原题','原答案',None,None)
  c.execute(text("UPDATE question_bank SET blanks=:value WHERE id='old'"),{'value':'[{"index":1,"answer":"氢"}]'})
  with pytest.raises(RuntimeError,match='拒绝'):command.downgrade(cfg,'202610092122')
 engine.dispose()
