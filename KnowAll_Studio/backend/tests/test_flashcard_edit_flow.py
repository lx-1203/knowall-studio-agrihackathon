"""Actual temporary SQLite/ASGI content edits; schedule and native CAS boundaries."""
import asyncio
from types import SimpleNamespace
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.api import flashcards
from app.core.auth import get_optional_user
from app.database import Base, get_db
from app.models import Deck, Flashcard, ReviewSchedule, ReviewLog, StudyPack

@pytest.fixture
async def edits(tmp_path, monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path/'edit.sqlite').as_posix())
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async def db():
        async with factory() as session:
            yield session
    actor = SimpleNamespace(id='A')
    app = FastAPI(); app.include_router(flashcards.router)
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_optional_user] = lambda: actor
    async def forbidden(*args, **kwargs):
        raise AssertionError('Content editing must not call AI')
    monkeypatch.setattr(flashcards.card_generator, 'generate', forbidden)
    from app.core.memory.retrieval import card_retriever
    async def empty(*args, **kwargs): return []
    monkeypatch.setattr(card_retriever, 'search', empty)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://edit') as client:
        yield client, factory, actor
    await engine.dispose()

async def create(client, kind='qa'):
    deck = await client.post('/api/v1/flashcards/decks', json={'name':'纠错牌组'})
    assert deck.status_code == 201
    content = {'card_type':kind, 'front':'{{c1::细胞核}} 为细胞供能' if kind=='cloze' else '细胞供能的结构是什么？', 'back':'错误答案细胞核', 'hints':'旧提示'}
    response = await client.post('/api/v1/flashcards/cards', json={**content,'deck_id':deck.json()['id'],'tags':['生物']})
    assert response.status_code == 201, response.text
    return response.json(), content

def payload(baseline, **values):
    return {'front':baseline['front'],'back':'正确答案：**线粒体**','hints':'看细胞呼吸','original':baseline,**values}

async def persisted(factory, cid):
    async with factory() as db:
        card=await db.get(Flashcard,cid)
        schedule=await db.scalar(select(ReviewSchedule).where(ReviewSchedule.card_id==cid))
        deck=await db.get(Deck,card.deck_id)
        values=lambda model: {col.name:getattr(model,col.name) for col in model.__table__.columns}
        return values(card),values(schedule),values(deck),await db.scalar(select(func.count(ReviewLog.id)).where(ReviewLog.card_id==cid))

@pytest.mark.parametrize('kind',['qa','cloze','compare'])
async def test_read_edit_all_types_preserves_review_and_identity(edits,kind):
    client,factory,_=edits;card,original=await create(client,kind);cid=card['id']
    assert (await client.post('/api/v1/flashcards/review',json={'card_id':cid,'rating':3})).status_code==200
    before=await persisted(factory,cid)
    response=await client.patch('/api/v1/flashcards/cards/'+cid,json=payload(original,card_type='qa',deck_id='forged',tags=['forged']))
    assert response.status_code==200,response.text
    detail=await client.get('/api/v1/flashcards/cards/'+cid);assert detail.status_code==200
    assert detail.json()['back']=='正确答案：**线粒体**' and detail.json()['card_type']==kind
    after=await persisted(factory,cid)
    assert {k:v for k,v in before[0].items() if k not in ['front','back','hints','content_version']}=={k:v for k,v in after[0].items() if k not in ['front','back','hints','content_version']}
    assert after[0]["content_version"] == before[0]["content_version"] + 1
    assert before[1:]==after[1:]
    assert (await client.get('/api/v1/flashcards/due')).json()['cards']==[]

async def test_conflict_does_not_overwrite_another_content(edits):
    client,factory,_=edits;card,original=await create(client);url='/api/v1/flashcards/cards/'+card['id']
    assert (await client.patch(url,json=payload(original))).status_code==200
    response=await client.patch(url,json=payload(original,back='旧设备再次覆盖'))
    assert response.status_code==409,response.text
    assert (await persisted(factory,card['id']))[0]['back']=='正确答案：**线粒体**'

async def test_two_real_requests_one_winner(edits):
    client,factory,_=edits;card,original=await create(client);url='/api/v1/flashcards/cards/'+card['id']
    results=await asyncio.gather(client.patch(url,json=payload(original,back='设备甲正确答案')),client.patch(url,json=payload(original,back='设备乙正确答案')))
    assert sorted(r.status_code for r in results)==[200,409],[(r.status_code,r.text) for r in results]
    assert (await persisted(factory,card['id']))[0]['back'] in ['设备甲正确答案','设备乙正确答案']

@pytest.mark.parametrize('values',[{'front':' '},{'front':'短'},{'back':''},{'back':'x'*20001},{'hints':'x'*1001},{'original':None}])
async def test_invalid_content_writes_nothing(edits,values):
    client,factory,_=edits;card,original=await create(client);before=await persisted(factory,card['id'])
    response=await client.patch('/api/v1/flashcards/cards/'+card['id'],json=payload(original,**values))
    assert response.status_code==422,response.text
    assert await persisted(factory,card['id'])==before

async def test_cloze_missing_marker_is_rejected(edits):
    client,factory,_=edits;card,original=await create(client,'cloze')
    response=await client.patch('/api/v1/flashcards/cards/'+card['id'],json=payload(original,front='不含填空标记的正文'))
    assert response.status_code==422,response.text

async def test_private_read_and_write_denied_and_legacy_null_hint_supported(edits):
    client,factory,actor=edits;card,original=await create(client);url='/api/v1/flashcards/cards/'+card['id']
    before=await persisted(factory,card['id']);actor.id='B'
    assert (await client.get(url)).status_code==404
    assert (await client.patch(url,json=payload(original))).status_code==404
    assert await persisted(factory,card['id'])==before
    async with factory() as db:
        (await db.get(Deck,card['deck_id'])).user_id=None
        (await db.get(Flashcard,card['id'])).hints=None
        await db.commit()
    detail=await client.get(url);assert detail.status_code==200 and detail.json()['hints']==''
    original['hints']='';assert (await client.patch(url,json=payload(original))).status_code==200

async def test_commit_failure_rolls_back_and_can_retry(edits,monkeypatch):
    client,factory,_=edits;card,original=await create(client);before=await persisted(factory,card['id']);url='/api/v1/flashcards/cards/'+card['id']
    real=AsyncSession.commit
    async def fail(self):raise RuntimeError('private database detail')
    monkeypatch.setattr(AsyncSession,'commit',fail)
    response=await client.patch(url,json=payload(original));assert response.status_code==500 and 'private database detail' not in response.text
    assert await persisted(factory,card['id'])==before
    monkeypatch.setattr(AsyncSession,'commit',real)
    assert (await client.patch(url,json=payload(original))).status_code==200

async def test_semantic_hit_displays_current_sql_content(edits,monkeypatch):
    client,_,_=edits;card,original=await create(client)
    assert (await client.patch('/api/v1/flashcards/cards/'+card['id'],json=payload(original))).status_code==200
    from app.core.memory.retrieval import card_retriever
    async def stale(*args,**kwargs):return [{'id':card['id'],'front':'旧索引正文','back':'旧索引答案','score':0.91}]
    monkeypatch.setattr(card_retriever,'search',stale)
    response=await client.get('/api/v1/flashcards/search',params={'q':'细胞'})
    assert response.status_code==200 and response.json()['search_method']=='semantic'
    hit=response.json()['results'][0];assert hit['back']=='正确答案：**线粒体**' and hit['score']==0.91

async def test_study_pack_owner_denies_read_and_write(edits):
    client,factory,actor=edits;card,original=await create(client)
    async with factory() as db:
        (await db.get(Deck,card['deck_id'])).user_id=None
        db.add(StudyPack(id='pack',title='个人学习包',document_id='doc',source_hash='test-only',user_id='A',deck_id=card['deck_id']))
        await db.commit()
    actor.id='B';url='/api/v1/flashcards/cards/'+card['id']
    assert (await client.get(url)).status_code==404
    assert (await client.patch(url,json=payload(original))).status_code==404

async def test_actual_export_content_changes_with_stable_guid(edits,tmp_path):
    import sqlite3,zipfile
    client,_,_=edits;card,original=await create(client)
    async def note(name):
        response=await client.get('/api/v1/flashcards/export/anki/'+card['deck_id'])
        assert response.status_code==200
        archive=tmp_path/(name+'.apkg');archive.write_bytes(response.content)
        with zipfile.ZipFile(archive) as z:
            database=tmp_path/(name+'.anki2');database.write_bytes(z.read('collection.anki2'))
        with sqlite3.connect(database) as db:return db.execute('SELECT guid,flds FROM notes').fetchone()
    before=await note('before')
    response=await client.patch('/api/v1/flashcards/cards/'+card['id'],json=payload(original))
    assert response.status_code==200
    after=await note('after');assert before[0]==after[0] and '<strong>线粒体</strong>' in after[1] and before[1]!=after[1]
