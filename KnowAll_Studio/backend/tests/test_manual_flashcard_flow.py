"""Real HTTP routes and temporary disk SQLite; forbid any generation calls."""
from types import SimpleNamespace
import asyncio
import pytest
import httpx
from fastapi import FastAPI
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.api import flashcards
from app.database import Base,get_db
from app.core.auth import get_optional_user
from app.models import Deck,Flashcard,StudyPack,Document,ReviewSchedule

@pytest.fixture
async def manual(tmp_path,monkeypatch):
    engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'manual.sqlite').as_posix())
    async with engine.begin() as c: await c.run_sync(Base.metadata.create_all)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory() as db:
        db.add(Document(id='doc',filename='book',file_type='md',sha256='f'*64))
        db.add_all([Deck(id=x,name=x,card_count=0) for x in ['own','private','shared']]);await db.flush()
        db.add_all([StudyPack(id='pack-'+d,user_id=u,document_id='doc',title=d,source_hash='f'*64,config={},sections=[],deck_id=d) for d,u in [('own','u1'),('private','u2')]])
        await db.commit()
    async def database():
        async with factory() as db: yield db
    async def forbidden(*a,**k): raise AssertionError('Manual create must never call AI')
    monkeypatch.setattr(flashcards.card_generator,'generate',forbidden)
    app=FastAPI();app.include_router(flashcards.router)
    app.dependency_overrides[get_db]=database
    app.dependency_overrides[get_optional_user]=lambda:SimpleNamespace(id='u1')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://manual') as client:
        yield client,factory
    await engine.dispose()

def payload(**more): return {'deck_id':'own','card_type':'qa','front':'线粒体的主要作用是什么？','back':'参与细胞呼吸，为细胞活动供能。','hints':'细胞结构','tags':['生物'],**more}

@pytest.mark.parametrize('kind,front',[('qa','线粒体的主要作用是什么？'),('cloze','{{c1::线粒体}} 为细胞活动供能'),('compare','比较线粒体与细胞核的作用')])
async def test_create_native_schedule_due_and_real_persistence(manual,kind,front):
    client,factory=manual
    response=await client.post('/api/v1/flashcards/cards',json=payload(card_type=kind,front=front))
    assert response.status_code==201,response.text
    card_id=response.json()['id']
    due=await client.get('/api/v1/flashcards/due',params={'deck_id':'own'})
    assert due.status_code==200 and due.json()['cards'][0]['id']==card_id
    async with factory() as db:
        card=await db.get(Flashcard,card_id);assert card.front==front and card.tags==['生物']
        schedule=(await db.execute(select(ReviewSchedule).where(ReviewSchedule.card_id==card_id))).scalar_one()
        assert schedule.state=='new' and schedule.fsrs_version=='fsrs-6' and schedule.review_count==0
        assert (await db.get(Deck,'own')).card_count==1
    graded=await client.post('/api/v1/flashcards/review',json={'card_id':card_id,'rating':3})
    assert graded.status_code==200 and graded.json()['review_count']==1
    assert (await client.get('/api/v1/flashcards/due',params={'deck_id':'own'})).json()['cards']==[]

@pytest.mark.parametrize('change',[{'front':'   '},{'back':''},{'front':'短'}, {'card_type':'random'}, {'card_type':'cloze','front':'没有任何填空标记的正面'}, {'front':'相同且不是有效问答的内容','back':'相同且不是有效问答的内容'}, {'tags':['ok',4]},{'front':'a'*20001},{'tags':['a']*21}])
async def test_rejected_content_writes_nothing(manual,change):
    client,factory=manual
    response=await client.post('/api/v1/flashcards/cards',json=payload(**change))
    assert response.status_code==422,response.text
    async with factory() as db:
        assert await db.scalar(select(func.count(Flashcard.id)))==0
        assert await db.scalar(select(func.count(ReviewSchedule.id)))==0
        assert (await db.get(Deck,'own')).card_count==0

@pytest.mark.parametrize('deck',['private','missing'])
async def test_private_or_missing_deck_has_no_write(manual,deck):
    client,factory=manual
    response=await client.post('/api/v1/flashcards/cards',json=payload(deck_id=deck))
    assert response.status_code==404,response.text
    async with factory() as db: assert await db.scalar(select(func.count(Flashcard.id)))==0

async def test_concurrent_same_deck_counts_and_schedules_are_atomic(manual):
    client,factory=manual
    responses=await asyncio.gather(*(client.post('/api/v1/flashcards/cards',json=payload(front=f'并发录入的独立问题第{i}张')) for i in range(8)))
    assert all(x.status_code==201 for x in responses),[x.text for x in responses]
    assert len({x.json()['id'] for x in responses})==8
    async with factory() as db:
        assert await db.scalar(select(func.count(Flashcard.id)))==8
        assert await db.scalar(select(func.count(ReviewSchedule.id)))==8
        assert (await db.get(Deck,'own')).card_count==8

async def test_commit_failure_rolls_back_card_schedule_and_count(manual,monkeypatch):
    client,factory=manual
    from sqlalchemy.ext.asyncio import AsyncSession
    async def fail(*a,**k): raise RuntimeError('test-only commit failure')
    monkeypatch.setattr(AsyncSession,'commit',fail)
    response=await client.post('/api/v1/flashcards/cards',json=payload())
    assert response.status_code==500,response.text
    assert 'test-only' not in response.text
    async with factory() as db:
        assert await db.scalar(select(func.count(Flashcard.id)))==0
        assert await db.scalar(select(func.count(ReviewSchedule.id)))==0
        assert (await db.get(Deck,'own')).card_count==0
