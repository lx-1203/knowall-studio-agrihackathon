"""Real HTTP/SQLite + actual Anki consumer, deterministic boundary clock only."""
import asyncio,importlib.util,io,sqlite3,time,zipfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from anki.collection import Collection,ImportAnkiPackageRequest
from app.models import Flashcard
from tests.test_flashcard_edit_flow import edits,create,payload,persisted

@pytest.fixture
def boundary(monkeypatch):
 clock=SimpleNamespace(now=1790000000.25,sleeps=[])
 monkeypatch.setattr('app.core.anki_export.time',SimpleNamespace(time=lambda:clock.now))
 if importlib.util.find_spec('app.core.anki_versions'):
  from app.core import anki_versions
  monkeypatch.setattr(anki_versions,'_clock',SimpleNamespace(time=lambda:clock.now,monotonic=time.monotonic))
  async def advance(delay):clock.sleeps.append(delay);clock.now+=delay
  monkeypatch.setattr(anki_versions,'_sleep',advance)
 return clock

async def package(client,deck):
 response=await client.get('/api/v1/flashcards/export/anki/'+deck)
 assert response.status_code==200,response.text
 return response

def note_rows(response,tmp_path,name):
 with zipfile.ZipFile(io.BytesIO(response.content)) as z:
  db=tmp_path/(name+'.sqlite');db.write_bytes(z.read('collection.anki2'))
 with sqlite3.connect(db) as conn:return conn.execute('select guid,mod,flds from notes').fetchall()

def consume(col,response,tmp_path,name):
 p=tmp_path/(name+'.apkg');p.write_bytes(response.content)
 return col.import_anki_package(ImportAnkiPackageRequest(package_path=str(p)))

@pytest.mark.parametrize('kind',['qa','cloze','compare'])
async def test_same_second_real_edit_default_anki_update_preserves_schedule(edits,boundary,tmp_path,kind):
 client,factory,_=edits;c,original=await create(client,kind);first=await package(client,c['deck_id'])
 col=Collection(str(tmp_path/'consumer.anki2'))
 try:
  consume(col,first,tmp_path,'first');ids=col.find_cards('');n=col.get_note(col.find_notes('')[0]);guid=n.guid
  card=col.get_card(ids[0]);card.queue=card.type=2;card.ivl=17;card.reps=4;card.due=35;col.update_card(card)
  assert (await client.patch('/api/v1/flashcards/cards/'+c['id'],json=payload(original))).status_code==200
  second=await package(client,c['deck_id']);consume(col,second,tmp_path,'second')
  assert '线粒体' in col.get_note(col.find_notes('')[0]).fields[1]
  assert col.find_cards('')==ids and col.note_count()==1 and col.get_note(col.find_notes('')[0]).guid==guid
  kept=col.get_card(ids[0]);assert (kept.ivl,kept.reps,kept.due,kept.queue)==(17,4,35,2)
  a=note_rows(first,tmp_path,'a');b=note_rows(second,tmp_path,'b');assert b[0][1]>a[0][1] and b[0][1]<=int(boundary.now)
  assert boundary.sleeps and sum(boundary.sleeps)<=2
 finally:col.close()

async def test_repeated_unchanged_export_and_new_sql_session_keep_mtime(edits,boundary,tmp_path):
 client,factory,_=edits;c,_=await create(client);a=await package(client,c['deck_id']);initial_sleeps=list(boundary.sleeps);boundary.now+=100
 b=await package(client,c['deck_id']);assert note_rows(a,tmp_path,'a')==note_rows(b,tmp_path,'b')
 async with factory() as db:
  stored=await db.get(Flashcard,c['id']);assert stored.anki_export_version==stored.content_version==1
  assert stored.anki_export_mtime==1790000001
 assert boundary.sleeps==initial_sleeps

async def test_noop_content_patch_does_not_make_anki_content_newer(edits,boundary,tmp_path):
 client,factory,_=edits;c,original=await create(client);a=await package(client,c['deck_id'])
 response=await client.patch('/api/v1/flashcards/cards/'+c['id'],json={**original,'original':original})
 assert response.status_code==200;boundary.now+=10;b=await package(client,c['deck_id'])
 assert note_rows(a,tmp_path,'a')==note_rows(b,tmp_path,'b')
 async with factory() as db:assert (await db.get(Flashcard,c['id'])).content_version==1

async def test_two_concurrent_requests_keep_one_version_same_timestamp(edits,boundary,tmp_path):
 client,factory,_=edits;c,_=await create(client)
 results=await asyncio.gather(package(client,c['deck_id']),package(client,c['deck_id']))
 assert note_rows(results[0],tmp_path,'a')==note_rows(results[1],tmp_path,'b')
 async with factory() as db:
  saved=await db.get(Flashcard,c['id']);assert saved.anki_export_version==1
  assert saved.anki_export_mtime==note_rows(results[0],tmp_path,'stored')[0][1]<=int(boundary.now)

@pytest.mark.parametrize('phase',['encoder','commit'])
async def test_failure_rolls_back_export_metadata_and_retry_works(edits,boundary,monkeypatch,phase):
 client,factory,_=edits;c,_=await create(client)
 from app.api import flashcards
 async def failcommit(*args,**kwargs):raise RuntimeError('private database failure')
 def failencoder(*args,**kwargs):raise RuntimeError('private encoder failure')
 with monkeypatch.context() as m:
  if phase=='commit':m.setattr(AsyncSession,'commit',failcommit)
  else:m.setattr(flashcards,'export_apkg',failencoder)
  response=await client.get('/api/v1/flashcards/export/anki/'+c['deck_id']);assert response.status_code==500 and 'private' not in response.text
 async with factory() as db:
  saved=await db.get(Flashcard,c['id']);assert saved.anki_export_version is None and saved.anki_export_mtime is None
 assert (await package(client,c['deck_id'])).status_code==200

async def test_clock_rollback_returns_retryable503_not_future_mtime(edits,boundary):
 client,factory,_=edits;c,original=await create(client);await package(client,c['deck_id'])
 assert (await client.patch('/api/v1/flashcards/cards/'+c['id'],json=payload(original))).status_code==200
 async with factory() as db:
  saved=await db.get(Flashcard,c['id']);saved.anki_export_mtime=int(boundary.now)+60;await db.commit()
 before=await persisted(factory,c['id']);response=await client.get('/api/v1/flashcards/export/anki/'+c['deck_id'])
 assert response.status_code==503 and response.headers['retry-after']=='1'
 assert await persisted(factory,c['id'])==before

async def test_foreign_account_export_cannot_advance_metadata(edits,boundary):
 client,factory,actor=edits;c,_=await create(client);before=await persisted(factory,c['id']);actor.id='B'
 assert (await client.get('/api/v1/flashcards/export/anki/'+c['deck_id'])).status_code==404
 assert await persisted(factory,c['id'])==before

async def test_first_versioned_export_updates_same_second_pre_upgrade_package(edits,boundary,tmp_path):
 client,_,_=edits;c,original=await create(client)
 from app.core.anki_export import export_apkg
 path=export_apkg([c],'纠错牌组',str(tmp_path/'legacy.apkg'),deck_id=c['deck_id'])
 col=Collection(str(tmp_path/'old-consumer.anki2'))
 try:
  col.import_anki_package(ImportAnkiPackageRequest(package_path=path))
  assert (await client.patch('/api/v1/flashcards/cards/'+c['id'],json=payload(original))).status_code==200
  updated=await package(client,c['deck_id']);consume(col,updated,tmp_path,'updated')
  assert '线粒体' in col.get_note(col.find_notes('')[0]).fields[1]
  assert col.note_count()==col.card_count()==1
 finally:col.close()

async def test_unchanged_clock_rollback_never_returns_future_package(edits,boundary):
 client,factory,_=edits;c,_=await create(client);await package(client,c['deck_id']);boundary.now-=60
 before=await persisted(factory,c['id']);response=await client.get('/api/v1/flashcards/export/anki/'+c['deck_id'])
 assert response.status_code==503
 assert await persisted(factory,c['id'])==before


async def test_cancel_before_encoder_returns_rolls_back_and_removes_owned_file(edits,boundary,monkeypatch):
 client,factory,_=edits;c,_=await create(client)
 from app.api import flashcards
 entered=asyncio.Event();paths=[];mkstemp=flashcards.tempfile.mkstemp
 def capture(*args,**kwargs):
  descriptor,path=mkstemp(*args,**kwargs);paths.append(Path(path));return descriptor,path
 async def pending_encoder(*args,**kwargs):
  entered.set();await asyncio.Event().wait()
 monkeypatch.setattr(flashcards.tempfile,'mkstemp',capture)
 monkeypatch.setattr(flashcards,'run_in_threadpool',pending_encoder)
 task=asyncio.create_task(client.get('/api/v1/flashcards/export/anki/'+c['deck_id']))
 await asyncio.wait_for(entered.wait(),5);task.cancel()
 with pytest.raises(asyncio.CancelledError):await task
 assert paths and all(not path.exists() for path in paths)
 async with factory() as db:
  card=await db.get(Flashcard,c['id']);assert card.anki_export_mtime is None and card.anki_export_version is None
