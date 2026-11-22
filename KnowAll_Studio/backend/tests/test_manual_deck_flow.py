"""Actual temporary SQLite/HTTP first deck; all existing card privacy callers."""
from types import SimpleNamespace
import pytest,httpx,asyncio
from fastapi import FastAPI
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker,AsyncSession
from app.api import flashcards
from app.database import Base,get_db
from app.core.auth import get_optional_user
from app.models import Deck,Flashcard,ReviewSchedule

@pytest.fixture
async def decks(tmp_path,monkeypatch):
 engine=create_async_engine('sqlite+aiosqlite:///'+(tmp_path/'decks.sqlite').as_posix())
 async with engine.begin() as c:await c.run_sync(Base.metadata.create_all)
 factory=async_sessionmaker(engine,expire_on_commit=False)
 async def db():
  async with factory() as session:yield session
 actor=SimpleNamespace(id='A')
 app=FastAPI();app.include_router(flashcards.router);app.dependency_overrides[get_db]=db;app.dependency_overrides[get_optional_user]=lambda:actor
 async def forbidden(*a,**k):raise AssertionError('First manual deck must not call AI')
 monkeypatch.setattr(flashcards.card_generator,'generate',forbidden)
 from app.core.memory.retrieval import card_retriever
 async def isolated_search(*a,**k):return []
 # Retrieval alone is stubbed; SQL fallback/privacy uses this temporary DB.
 monkeypatch.setattr(card_retriever,'search',isolated_search)
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://decks') as client:yield client,factory,actor
 await engine.dispose()

async def create(client,**more):
 response=await client.post('/api/v1/flashcards/decks',json={'name':' 我的手工牌组 ','description':'补充知识',**more})
 assert response.status_code==201,response.text
 return response.json()

async def test_first_deck_no_document_and_trusted_owner_persist(decks):
 client,factory,actor=decks;assert (await client.get('/api/v1/flashcards/decks')).json()==[]
 deck=await create(client,user_id='B');assert deck['name']=='我的手工牌组' and deck['card_count']==0
 async with factory() as db:
  stored=await db.get(Deck,deck['id']);assert stored.user_id=='A' and stored.description=='补充知识'
 assert [d['id'] for d in (await client.get('/api/v1/flashcards/decks')).json()]==[deck['id']]
 actor.id='B';assert (await client.get('/api/v1/flashcards/decks')).json()==[]

@pytest.mark.parametrize('change',[{'name':''},{'name':'   '},{'name':'x'*201},{'description':'x'*2001},{'name':3}])
async def test_bad_name_or_description_writes_no_deck(decks,change):
 client,factory,_=decks;r=await client.post('/api/v1/flashcards/decks',json={'name':'正常牌组',**change});assert r.status_code==422,r.text
 async with factory() as db:assert await db.scalar(select(func.count(Deck.id)))==0

@pytest.mark.parametrize('kind,front',[('qa','细胞呼吸有什么作用？'),('cloze','{{c1::线粒体}} 为细胞供能'),('compare','比较细胞核和线粒体的作用')])
async def test_first_card_real_due_review_and_all_private_routes(decks,kind,front):
 client,factory,actor=decks;deck=await create(client)
 body={'deck_id':deck['id'],'card_type':kind,'front':front,'back':'标准解释'}
 card=await client.post('/api/v1/flashcards/cards',json=body);assert card.status_code==201
 cid=card.json()['id'];assert (await client.get('/api/v1/flashcards/due')).json()['cards'][0]['id']==cid
 actor.id='B'
 assert (await client.get('/api/v1/flashcards/due')).json()['cards']==[]
 for url,body in [('/api/v1/flashcards/cards',body),('/api/v1/flashcards/review',{'card_id':cid,'rating':3})]:assert (await client.post(url,json=body)).status_code==404
 assert (await client.get('/api/v1/flashcards/export/anki/'+deck['id'])).status_code==404
 assert (await client.get('/api/v1/flashcards/related/'+cid)).status_code==404
 assert (await client.get('/api/v1/flashcards/search',params={'q':front})).json()['results']==[]
 actor.id='A';assert (await client.post('/api/v1/flashcards/review',json={'card_id':cid,'rating':3})).json()['review_count']==1
 assert (await client.get('/api/v1/flashcards/due')).json()['cards']==[]

async def test_legacy_null_shared_and_local_owner_compatibility(decks):
 client,factory,actor=decks
 async with factory() as db:db.add(Deck(id='legacy',name='旧共享'));await db.commit()
 deck=await create(client);actor.id='B';assert [d['id'] for d in (await client.get('/api/v1/flashcards/decks')).json()]==['legacy']
 actor.id=None;local=await create(client,name='本机牌组')
 async with factory() as db:assert (await db.get(Deck,local['id'])).user_id=='local_user'
 assert {d['id'] for d in (await client.get('/api/v1/flashcards/decks')).json()}=={'legacy',local['id']}

async def test_independent_same_name_decks_and_commit_failure(decks,monkeypatch):
 client,factory,_=decks
 first=await create(client);second=await create(client);assert first['id']!=second['id']
 async def fail(*a,**k):raise RuntimeError('test-only private detail')
 monkeypatch.setattr(AsyncSession,'commit',fail)
 response=await client.post('/api/v1/flashcards/decks',json={'name':'失败牌组'});assert response.status_code==500 and 'private detail' not in response.text
 async with factory() as db:assert await db.scalar(select(func.count(Deck.id)))==2
