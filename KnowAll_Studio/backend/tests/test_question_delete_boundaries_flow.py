"""Actual delete routers/SQLite: FK constraints, source cleanup, atomic failures. No AI."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest,httpx
from fastapi import FastAPI
from sqlalchemy import event,select,text,func,delete
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker,AsyncSession
from sqlalchemy.exc import IntegrityError
from app.database import Base,get_db
from app.models import Document,KnowledgeSummary,KnowledgePointNode,KnowledgeCoverage,QuestionBank,AnswerRecord,ErrorLog,LiveSession,LiveQuestion
from app.api import questions as bank
from app.core.api_scheduler import api_client
BASE='/api/v1/quiz/bank'

@pytest.fixture
async def flow(tmp_path,monkeypatch):
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'delete.sqlite'))
    @event.listens_for(engine.sync_engine,'connect')
    def fk(connection,*args):connection.execute('PRAGMA foreign_keys=ON')
    async with engine.begin() as c:await c.run_sync(Base.metadata.create_all)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as db:
        db.add(Document(id='doc',filename='fixture.md',file_type='md',sha256='delete-source'));await db.commit()
        db.add(KnowledgeSummary(id='sum',document_id='doc',content_md='来源'));await db.commit()
        db.add(KnowledgePointNode(id='kp',document_id='doc',summary_id='sum',level=1,sequence=1,title='源知识点',explanation='测试来源'))
        db.add_all([QuestionBank(id=f'q{i}',question_type='single_choice',question_text=f'删除测试第{i}题',correct_answer='A',options=[{'label':'A','text':'甲'},{'label':'B','text':'乙'}]) for i in range(1,4)])
        db.add(LiveSession(id='live',host_id='B',title='测试课堂',room_code='TEST01'));await db.commit()
        db.add_all([KnowledgeCoverage(id='cov'+str(i),knowledge_point_id='kp',resource_type='question',resource_id=f'q{i}') for i in [1,2,3]])
        db.add(KnowledgeCoverage(id='card-cov',knowledge_point_id='kp',resource_type='flashcard',resource_id='q1'));await db.commit()
    async def db():
        async with factory() as s:yield s
    app=FastAPI();app.include_router(bank.router);app.dependency_overrides[get_db]=db
    monkeypatch.setattr(api_client,'generate',AsyncMock(side_effect=AssertionError('No AI in deletion flow')))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as client:
        yield SimpleNamespace(client=client,factory=factory,engine=engine)
    await engine.dispose()

async def foreign_keys(f,on):
    # Every checkout explicitly sets requested mode; assert actual state.
    def enable(connection,*args):connection.execute('PRAGMA foreign_keys='+('ON' if on else 'OFF'))
    event.listen(f.engine.sync_engine,'checkout',enable)
    async with f.factory() as db:assert (await db.execute(text('PRAGMA foreign_keys'))).scalar()==int(on)

async def state(f):
    async with f.factory() as db:
        return {'questions':sorted((await db.scalars(select(QuestionBank.id))).all()),
                'coverage':sorted((await db.scalars(select(KnowledgeCoverage.id))).all()),
                'answers':await db.scalar(select(func.count()).select_from(AnswerRecord)),
                'errors':await db.scalar(select(func.count()).select_from(ErrorLog)),
                'live':await db.scalar(select(func.count()).select_from(LiveQuestion))}

async def remove(f,mode,ids):
    return await f.client.delete(BASE+'/'+ids[0]) if mode=='single' else await f.client.post(BASE+'/delete-batch',json=ids)

async def refer(f,kind):
    async with f.factory() as db:
        row=AnswerRecord(question_id='q1',user_id='B',user_answer='B',is_correct=False) if kind=='answer' else ErrorLog(question_id='q1',user_id='B') if kind=='error' else LiveQuestion(session_id='live',question_id='q1',question_index=0)
        db.add(row);await db.commit()

@pytest.mark.parametrize('fk',[True,False])
@pytest.mark.parametrize('mode',['single','batch'])
@pytest.mark.parametrize('kind',['answer','error','live'])
async def test_referenced_question_is_conflict_and_batch_is_all_or_nothing(flow,fk,mode,kind):
    await foreign_keys(flow,fk);await refer(flow,kind);before=await state(flow)
    r=await remove(flow,mode,['q1','q2']);assert r.status_code==409,r.text
    assert '关联' in r.json()['detail'];assert await state(flow)==before
    # A different unreferenced question can still be removed in a fresh request.
    assert (await remove(flow,'single',['q3'])).status_code==200

@pytest.mark.parametrize('fk',[True,False])
@pytest.mark.parametrize('mode',['single','batch'])
async def test_delete_cleans_only_matching_question_coverage_and_counts_actual_rows(flow,fk,mode):
    await foreign_keys(flow,fk)
    r=await remove(flow,mode,['q1','q1','missing']);assert r.status_code==200,r.text
    if mode=='batch':assert r.json()['deleted_count']==1
    after=await state(flow);assert after['questions']==['q2','q3'];assert after['coverage']==['card-cov','cov2','cov3']
    assert (await remove(flow,'single',['q1'])).status_code==404
    assert (await remove(flow,'batch',['q1','missing'])).json()['deleted_count']==0
    assert (await remove(flow,'batch',[])).json()['deleted_count']==0

@pytest.mark.parametrize('mode',['single','batch'])
@pytest.mark.parametrize('failure',['commit','delete'])
async def test_transaction_fault_never_partially_deletes_sources_and_retry_succeeds(flow,monkeypatch,mode,failure):
    before=await state(flow);original_commit=AsyncSession.commit;original_execute=AsyncSession.execute;failed=False
    async def commit(db,*args,**kw):
        nonlocal failed
        if failure=='commit' and not failed:failed=True;raise RuntimeError('Explicit commit fault')
        return await original_commit(db,*args,**kw)
    async def execute(db,statement,*args,**kw):
        nonlocal failed
        if failure=='delete' and not failed and getattr(statement,'is_delete',False) and statement.table.name=='question_bank':failed=True;raise RuntimeError('Explicit delete fault')
        return await original_execute(db,statement,*args,**kw)
    monkeypatch.setattr(AsyncSession,'commit',commit);monkeypatch.setattr(AsyncSession,'execute',execute)
    r=await remove(flow,mode,['q1','q2']);assert r.status_code==500;assert await state(flow)==before
    assert (await remove(flow,mode,['q1','q2'])).status_code==200
    after=await state(flow);assert after['questions']==(['q2','q3'] if mode=='single' else ['q3'])
    assert after['coverage']==(['card-cov','cov2','cov3'] if mode=='single' else ['card-cov','cov3'])

@pytest.mark.parametrize('mode',['single','batch'])
async def test_actual_fk_reference_added_after_preflight_returns_conflict_and_rolls_back(flow,monkeypatch,mode):
    # SQLite deferred read transactions can observe this committed writer before
    # their first DELETE. Hook only the application DML boundary; actual FK fails.
    original=AsyncSession.execute;original_delete=AsyncSession.delete;inserted=False
    async def inject():
        nonlocal inserted
        if inserted:return
        inserted=True
        async with flow.factory() as other:
            other.add(AnswerRecord(question_id='q1',user_id='B',user_answer='B',is_correct=False));await other.commit()
    async def execute(db,statement,*args,**kw):
        nonlocal inserted
        if not inserted and getattr(statement,'is_delete',False):
            await inject()
        return await original(db,statement,*args,**kw)
    async def orm_delete(db,instance):
        await inject();return await original_delete(db,instance)
    monkeypatch.setattr(AsyncSession,'execute',execute)
    monkeypatch.setattr(AsyncSession,'delete',orm_delete)
    r=await remove(flow,mode,['q1','q2']);assert r.status_code==409,r.text
    after=await state(flow);assert after['questions']==['q1','q2','q3'];assert after['coverage']==['card-cov','cov1','cov2','cov3'];assert after['answers']==1
    async with flow.factory() as db:await db.execute(delete(AnswerRecord));await db.commit()
    assert (await remove(flow,mode,['q1','q2'])).status_code==200
