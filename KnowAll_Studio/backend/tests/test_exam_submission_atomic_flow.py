"""Real independent SQL connections/JWT/routing; grading gates and failures explicit."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import select,func,event,text,create_engine
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker,AsyncSession
from app.api import questions
from app.database import Base,get_db
from app.models import User,QuestionBank,ExamPaper,AnswerRecord,ErrorLog
from app.core.auth import create_access_token
from app.core.api_scheduler import api_client

@pytest.fixture
async def native(tmp_path,monkeypatch):
 engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'exam.sqlite'),connect_args={'timeout':5})
 @event.listens_for(engine.sync_engine,'connect')
 def pragmas(conn,*args):conn.execute('PRAGMA foreign_keys=ON');conn.execute('PRAGMA journal_mode=WAL')
 async with engine.begin() as c:await c.run_sync(Base.metadata.create_all)
 factory=async_sessionmaker(engine,expire_on_commit=False)
 async with factory() as db:
  db.add_all([User(id=u,username='09c3-'+u,email=u+'@fixture.invalid',password_hash='unused') for u in ['A','B']]);await db.flush()
  db.add_all([QuestionBank(id='q'+str(i),question_type='single_choice',question_text='真实评分题'+str(i),options=[{'label':c,'text':'选项'+c} for c in 'AB'],correct_answer='A') for i in range(2)])
  await db.flush();db.add_all([ExamPaper(id=p,title=p,question_ids=['q0','q1']) for p in ['p','other']]);db.add(ErrorLog(user_id='A',question_id='q0',error_count=3));await db.commit()
 app=FastAPI();app.include_router(questions.router)
 async def dbdep():
  async with factory() as db:yield db
 app.dependency_overrides[get_db]=dbdep
 @app.exception_handler(RuntimeError)
 async def failure(req,exc):return JSONResponse(status_code=500,content={'detail':'Explicit fixture failure'})
 calls=[]
 async def forbidden(*a,**kw):calls.append('forbidden');raise AssertionError('No AI calls allowed')
 monkeypatch.setattr(api_client,'generate',forbidden)
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://fixture.invalid') as client:
  try:yield client,factory,calls
  finally:await engine.dispose()

async def submit(client,user='A',paper='p'):
 return await client.post('/api/v1/quiz/exam/submit',headers={'Authorization':'Bearer '+create_access_token(user)},json={'paper_id':paper,'answers':{'q0':'B','q1':'A'},'enable_semantic':False})

async def records(factory):
 async with factory() as db:
  answers=(await db.scalars(select(AnswerRecord))).all();errors=(await db.scalars(select(ErrorLog))).all()
  table=Base.metadata.tables.get('exam_submissions');guards=(await db.execute(select(table))).all() if table is not None else []
  return answers,errors,guards

def gate_grading(monkeypatch,count):
 original=questions.exam_engine.grade_enhanced;entered=0;gate=asyncio.Event()
 async def grade(*a,**kw):
  nonlocal entered
  entered+=1
  if entered==count:gate.set()
  await asyncio.wait_for(gate.wait(),3)
  return await original(*a,**kw)
 monkeypatch.setattr(questions.exam_engine,'grade_enhanced',grade)

@pytest.mark.parametrize('count',[2,3,5])
async def test_concurrent_same_user_paper_commits_once(native,monkeypatch,count):
 client,factory,calls=native;gate_grading(monkeypatch,count)
 responses=await asyncio.gather(*[submit(client) for _ in range(count)])
 assert sorted(r.status_code for r in responses)==[200]+[400]*(count-1),[(r.status_code,r.text) for r in responses]
 winner=next(r.json() for r in responses if r.status_code==200);assert winner['score']==5 and len(winner['details'])==2
 answers,errors,guards=await records(factory);assert len(answers)==2 and len(guards)==1
 assert len(errors)==1 and errors[0].error_count==4;assert not calls

@pytest.mark.parametrize('scenario',['users','papers'])
async def test_distinct_submission_scopes_can_both_commit(native,monkeypatch,scenario):
 client,factory,calls=native;gate_grading(monkeypatch,2)
 second=('B','p') if scenario=='users' else ('A','other')
 responses=await asyncio.gather(submit(client),submit(client,*second));assert [r.status_code for r in responses]==[200,200]
 answers,errors,guards=await records(factory);assert len(answers)==4 and len(guards)==2
 assert (sorted(e.error_count for e in errors)==[1,4]) if scenario=='users' else (errors[0].error_count==5)
 assert not calls

async def test_sequential_duplicate_does_not_regrade_or_write(native,monkeypatch):
 client,factory,calls=native;assert (await submit(client)).status_code==200
 async def forbidden(*a,**kw):raise AssertionError('Duplicate must not regrade')
 monkeypatch.setattr(questions.exam_engine,'grade_enhanced',forbidden)
 response=await submit(client);assert response.status_code==400
 a,e,g=await records(factory);assert len(a)==2 and len(g)==1 and e[0].error_count==4

async def test_legacy_answer_without_guard_remains_duplicate(native,monkeypatch):
 client,factory,calls=native
 async with factory() as db:db.add(AnswerRecord(user_id='A',paper_id='p',question_id='q0',user_answer='B',is_correct=False));await db.commit()
 async def forbidden(*a,**kw):raise AssertionError('Legacy duplicate must not regrade')
 monkeypatch.setattr(questions.exam_engine,'grade_enhanced',forbidden)
 assert (await submit(client)).status_code==400
 a,e,g=await records(factory);assert len(a)==1 and len(g)==0 and e[0].error_count==3

async def test_grade_failure_then_manual_retry_is_allowed(native,monkeypatch):
 client,factory,calls=native;original=questions.exam_engine.grade_enhanced
 async def fail(*a,**kw):raise RuntimeError('Explicit grading failure')
 monkeypatch.setattr(questions.exam_engine,'grade_enhanced',fail);assert (await submit(client)).status_code==500
 a,e,g=await records(factory);assert not a and not g and e[0].error_count==3
 monkeypatch.setattr(questions.exam_engine,'grade_enhanced',original);assert (await submit(client)).status_code==200

async def test_commit_failure_rolls_back_guard_answers_and_error_count(native,monkeypatch):
 client,factory,calls=native;original=AsyncSession.commit;armed=True
 async def fail(db):
  nonlocal armed
  if armed:armed=False;raise RuntimeError('Explicit commit failure')
  return await original(db)
 monkeypatch.setattr(AsyncSession,'commit',fail);assert (await submit(client)).status_code==500
 a,e,g=await records(factory);assert not a and not g and e[0].error_count==3
 assert (await submit(client)).status_code==200;a,e,g=await records(factory);assert len(a)==2 and len(g)==1 and e[0].error_count==4

async def test_cancel_after_actual_guard_flush_releases_transaction(native,monkeypatch):
 client,factory,calls=native;original=AsyncSession.flush;entered=asyncio.Event()
 async def held(db,*a,**kw):
  guard=any(v.__tablename__=='exam_submissions' for v in db.new)
  result=await original(db,*a,**kw)
  if guard:entered.set();await asyncio.Event().wait()
  return result
 monkeypatch.setattr(AsyncSession,'flush',held)
 task=asyncio.create_task(submit(client))
 try:
  await asyncio.wait_for(entered.wait(),3);task.cancel()
  with pytest.raises(asyncio.CancelledError):await task
 finally:
  if not task.done():task.cancel()
  await asyncio.gather(task,return_exceptions=True)
 monkeypatch.setattr(AsyncSession,'flush',original)
 a,e,g=await records(factory);assert not a and not g and e[0].error_count==3
 assert (await submit(client)).status_code==200

async def test_unrelated_integrity_failure_is_not_reported_as_duplicate(native,monkeypatch):
 from sqlalchemy.exc import IntegrityError
 client,factory,calls=native;original=AsyncSession.flush;armed=True
 async def fail(db,*a,**kw):
  nonlocal armed
  if armed:armed=False;raise IntegrityError('explicit fixture unrelated constraint',{},RuntimeError('unrelated'))
  return await original(db,*a,**kw)
 monkeypatch.setattr(AsyncSession,'flush',fail);response=await submit(client);assert response.status_code==500
 a,e,g=await records(factory);assert not a and not g and e[0].error_count==3
 assert (await submit(client)).status_code==200

async def test_missing_paper_and_foreign_private_question_do_not_reserve(native):
 client,factory,calls=native;assert (await submit(client,paper='missing')).status_code==404
 async with factory() as db:
  q=await db.get(QuestionBank,'q0');q.review_scores={'knowledge_owner_id':'B'};await db.commit()
 assert (await submit(client)).status_code==404
 a,e,g=await records(factory);assert not a and not g and not calls
