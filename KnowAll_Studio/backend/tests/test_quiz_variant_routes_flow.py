"""Real JWT/SQL/quiz routers. Generator seam explicitly simulated for invalid batches."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx,pytest
from fastapi import FastAPI
from sqlalchemy import select,func,event
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.database import Base,get_db
from app.models import QuestionBank,ErrorLog
from app.models.user import User
from app.api import questions as api
from app.core.auth import create_access_token
from app.core.quiz import exam_engine
from tests.test_quiz_rubric_persistence_flow import item

@pytest.fixture
async def variant_flow(tmp_path,monkeypatch):
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'variant.sqlite').as_posix())
    @event.listens_for(engine.sync_engine,'connect')
    def foreign_keys(c,_):c.execute('PRAGMA foreign_keys=ON')
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with engine.begin() as c:await c.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add_all([User(id=k,username=k,email=k+'@invalid.test',password_hash='unused') for k in ('alice','bob')])
        db.add(QuestionBank(id='original',question_type='fill_blank',question_text='原题元素___和___',correct_answer='原甲;原乙',blanks=[{'index':1,'answer':'原甲'},{'index':2,'answer':'原乙'}]))
        await db.flush();db.add(ErrorLog(id='error',user_id='alice',question_id='original'));await db.commit()
    async def database():
        async with factory() as db:yield db
    app=FastAPI();app.include_router(api.router);app.dependency_overrides[get_db]=database
    generated=AsyncMock(return_value=[item()]);keys=AsyncMock()
    monkeypatch.setattr(api.quiz_generator,'generate_variants',generated)
    monkeypatch.setattr(api,'load_user_api_keys',keys)
    monkeypatch.setattr(exam_engine,'grade_semantic',AsyncMock(side_effect=AssertionError('Real external AI forbidden')))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://isolated') as client:
        yield SimpleNamespace(client=client,factory=factory,generated=generated,keys=keys,
            headers={k:{'Authorization':'Bearer '+create_access_token(k)} for k in ('alice','bob')})
    await engine.dispose()

async def invoke(f,user='alice',count=3):
    return await f.client.post('/api/v1/quiz/errors/error/variants',params={'count':count},headers=f.headers.get(user,{}))

@pytest.mark.parametrize('user',['bob','anonymous'])
async def test_foreign_error_hidden_before_loading_keys_or_generation(variant_flow,user):
    f=variant_flow;r=await invoke(f,user);assert r.status_code==404,r.text
    f.keys.assert_not_awaited();f.generated.assert_not_awaited()

@pytest.mark.parametrize('count',[0,-1,101])
async def test_invalid_count_rejected_without_generation(variant_flow,count):
    f=variant_flow;r=await invoke(f,count=count);assert r.status_code==422,r.text
    f.keys.assert_not_awaited();f.generated.assert_not_awaited()

@pytest.mark.parametrize('kind,answer,ratio',[('fill_blank','氢;错',.5),('short_answer','reliable',.75),('term_definition','reliable',.75)])
async def test_variant_rules_persist_and_grade_in_new_exam(variant_flow,kind,answer,ratio):
    f=variant_flow
    async with f.factory() as db:
        q=await db.get(QuestionBank,'original');q.question_type=kind;await db.commit()
    f.generated.return_value=[item(kind)]
    r=await invoke(f);assert r.status_code==200,r.text
    generated=r.json()['questions'][0];assert generated['blanks']==item(kind)['blanks'];assert generated['scoring_points']==item(kind)['scoring_points']
    original=f.generated.call_args.args[0];assert original['blanks'][0]['answer']=='原甲'
    async with f.factory() as db:
        q=await db.get(QuestionBank,generated['id']);assert q.parent_question_id=='original';assert q.blanks==item(kind)['blanks'];assert q.scoring_points==item(kind)['scoring_points']
    r=await f.client.post('/api/v1/quiz/exam/create',json={'question_ids':[generated['id']]},headers=f.headers['alice']);assert r.status_code==200,r.text
    paper=r.json();assert paper['questions'][0]['blanks']==item(kind)['blanks']
    r=await f.client.post('/api/v1/quiz/exam/submit',json={'paper_id':paper['paper_id'],'answers':{generated['id']:answer},'enable_semantic':False},headers=f.headers['alice']);assert r.status_code==200,r.text
    assert r.json()['details'][0]['partial_score']==ratio
    assert r.json()['percentage']==ratio*100

@pytest.mark.parametrize('bad',[[],[None],[item(),{**item(),'blanks':{}}],[item(),{**item(),'question_type':'short_answer'}],[{**item(),'question_text':''}]])
async def test_bad_batch_leaves_no_partial_rows_or_success_marker(variant_flow,bad):
    f=variant_flow;f.generated.return_value=bad;r=await invoke(f);assert r.status_code==502,r.text
    async with f.factory() as db:
        assert await db.scalar(select(func.count()).select_from(QuestionBank))==1
        assert (await db.get(ErrorLog,'error')).variant_generated is False

async def test_valid_blank_only_variant_synthesizes_aggregate_answer(variant_flow):
    f=variant_flow;q=item();q.pop('answer');f.generated.return_value=[q]
    r=await invoke(f);assert r.status_code==200,r.text;assert r.json()['questions'][0]['answer']=='氢;氧'
