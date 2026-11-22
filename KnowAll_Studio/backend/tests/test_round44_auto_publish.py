"""Real concurrent publisher/SQLite/RSA and native source-lock behavior."""
import asyncio,threading,hashlib
from pathlib import Path
import pytest
from filelock import FileLock
from sqlalchemy import func,select
from kaf.format import unpack_kaf
from app.core import kaf_publisher,kaf_service,crypto
from app.models.kaf import KafFile
from tests.test_product_auth import account_app
from tests.test_round41_key_recovery import private_kaf

@pytest.fixture
async def publisher(account_app,private_kaf,tmp_path,monkeypatch):
 service=kaf_service.KAFService();service.public_key
 monkeypatch.setattr(kaf_publisher,'get_kaf_service',lambda:service)
 monkeypatch.setattr(kaf_service,'KAF_DIR',tmp_path/'documents')
 async with account_app.sessions() as db:
  async with db.bind.begin() as conn:await conn.run_sync(KafFile.__table__.create)
 return account_app,service,tmp_path/'documents'

async def publish(db,kind,source='same44'):
 if kind=='document':return await kaf_publisher.publish_document(db,source,'lesson.md',markdown='# Concurrent44\n\nreal-content44')
 return await kaf_publisher.publish_summary_package(db,source,'Summary44','test44',[{'title':'Chapter44','body':'real-content44'}])

@pytest.mark.parametrize('kind',['document','summary'])
async def test_same_source_concurrency_publishes_one_real_file(publisher,monkeypatch,kind):
 app,service,directory=publisher
 method='publish_from_markdown' if kind=='document' else 'publish_from_package'
 original=getattr(service,method);release=threading.Event();calls=[]
 def gate(*args,**kwargs):
  calls.append(threading.get_ident());release.wait(.3)
  return original(*args,**kwargs)
 monkeypatch.setattr(service,method,gate)
 async def request():
  async with app.sessions() as db:return await publish(db,kind)
 tasks=[asyncio.create_task(request()) for _ in range(4)]
 try:
  results=await asyncio.wait_for(asyncio.gather(*tasks),8)
 finally:
  release.set()
  for t in tasks:
   if not t.done():t.cancel()
  await asyncio.gather(*tasks,return_exceptions=True)
 assert len(calls)==1,f'Concurrent same-source crypto ran {len(calls)} times'
 assert sum(r is not None for r in results)==1
 async with app.sessions() as db:
  rows=(await db.scalars(select(KafFile))).all();assert len(rows)==1
 assert len(list(directory.glob('*.kaf')))==1
 data=Path(rows[0].storage_path).read_bytes();decoded=unpack_kaf(data,service.private_key)
 assert decoded['checksum_ok'] and b'real-content44' in decoded['payload_bytes']

@pytest.mark.parametrize('kind',['document','summary'])
async def test_cancel_while_waiting_source_lock_never_publishes_late(publisher,kind):
 app,service,directory=publisher
 source='cancel44';source_id=source if kind=='document' else 'summary:'+source
 locks=directory/'.publish-locks';locks.mkdir(parents=True)
 lock=FileLock(str(locks/(hashlib.sha256(source_id.encode()).hexdigest()+'.lock')))
 lock.acquire(timeout=1)
 async with app.sessions() as db:
  task=asyncio.create_task(publish(db,kind,source))
  try:
   # A short meaningful observation window while the actual OS lock is held.
   done,_=await asyncio.wait({task},timeout=.18)
   assert not done,'Publisher bypassed the real source lock'
   task.cancel()
   with pytest.raises(asyncio.CancelledError):await task
  finally:
   lock.release()
   if not task.done():task.cancel()
   await asyncio.gather(task,return_exceptions=True)
 async with app.sessions() as db:
  assert await db.scalar(select(func.count()).select_from(KafFile))==0
  assert not list(directory.glob('*.kaf'))
  assert await publish(db,kind,source)
  assert await publish(db,kind,source) is None

async def test_different_sources_publish_in_parallel(publisher,monkeypatch):
 app,service,_=publisher
 original=service.publish_from_markdown;barrier=threading.Barrier(2)
 def gate(*args,**kwargs):barrier.wait(timeout=2);return original(*args,**kwargs)
 monkeypatch.setattr(service,'publish_from_markdown',gate)
 async def request(source):
  async with app.sessions() as db:return await publish(db,'document',source)
 result=await asyncio.gather(request('parallel-a44'),request('parallel-b44'))
 assert all(result) and result[0]['file_id']!=result[1]['file_id']

@pytest.mark.parametrize('kind',['document','summary'])
async def test_lock_timeout_stays_optional_then_recovers(publisher,monkeypatch,kind):
 from filelock import AsyncFileLock
 app,service,directory=publisher
 # Production supplies timeout explicitly; override only that default at the seam.
 def bounded(path,**kwargs):return AsyncFileLock(path,timeout=.03)
 monkeypatch.setattr(kaf_publisher,'AsyncFileLock',bounded)
 source='timeout44';source_id=source if kind=='document' else 'summary:'+source
 locks=directory/'.publish-locks';locks.mkdir(parents=True)
 lock=FileLock(str(locks/(hashlib.sha256(source_id.encode()).hexdigest()+'.lock')))
 lock.acquire(timeout=1)
 try:
  async with app.sessions() as db:
   assert await publish(db,kind,source) is None
   assert await db.scalar(select(func.count()).select_from(KafFile))==0
   assert not list(directory.glob('*.kaf'))
 finally:lock.release()
 async with app.sessions() as db:assert await publish(db,kind,source)

async def test_legacy_duplicate_rows_skip_without_new_file(publisher):
 app,service,directory=publisher
 async with app.sessions() as db:
  for number in range(2):
   legacy=service.publish_from_markdown('# Legacy44','test',source_doc_id='legacy44')
   # Post-migration history retains one anchor and an unclaimed duplicate.
   if number:legacy.source_claim=None
   db.add(legacy)
  await db.commit()
  before=set(directory.glob('*.kaf'))
  assert await publish(db,'document','legacy44') is None
  assert set(directory.glob('*.kaf'))==before
  assert await db.scalar(select(func.count()).select_from(KafFile))==2

async def test_source_lock_directory_failure_keeps_upload_optional(publisher,monkeypatch):
 app,_,directory=publisher
 mkdir=Path.mkdir
 def denied(path,*args,**kwargs):
  if path==directory/'.publish-locks':raise PermissionError('synthetic44-lock-directory-permission')
  return mkdir(path,*args,**kwargs)
 with monkeypatch.context() as patch:
  patch.setattr(Path,'mkdir',denied)
  async with app.sessions() as db:
   assert await publish(db,'document','io44') is None
   assert await db.scalar(select(func.count()).select_from(KafFile))==0
 assert not list(directory.glob('*.kaf'))
 async with app.sessions() as db:assert await publish(db,'document','io44')

async def test_database_commit_error_propagates_and_releases_source_lock(publisher,monkeypatch):
 app,_,directory=publisher
 async with app.sessions() as db:
  async def commit_failure():raise RuntimeError('synthetic44-database-commit-error')
  with monkeypatch.context() as patch:
   patch.setattr(db,'commit',commit_failure)
   with pytest.raises(RuntimeError,match='database-commit-error'):
    await publish(db,'document','db44')
  await db.rollback()
  assert await db.scalar(select(func.count()).select_from(KafFile))==0
  # The pre-existing file-before-DB pipeline can leave an orphan on commit
  # failure; this round preserves propagation and never pretends atomicity.
  assert len(list(directory.glob('*.kaf')))==1
  assert await publish(db,'document','db44')
