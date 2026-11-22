"""Native database and authenticated API; fault injection only at commit boundary."""
import asyncio
from pathlib import Path
import pytest
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError, IntegrityError
from app.core import kaf_service
from app.models.kaf import KafFile
from tests.test_round44_auto_publish import publisher, publish, account_app, private_kaf
from tests.test_round43_kaf_recovery import real_kaf_http

@pytest.mark.parametrize('kind',['document','summary'])
async def test_committed_response_loss_returns_original_identity(publisher, monkeypatch, kind):
 app,service,directory=publisher
 async with app.sessions() as db:
  commit=db.commit
  async def lost():
   await commit();raise ConnectionError('private-driver-secret50')
  monkeypatch.setattr(db,'commit',lost)
  result=await publish(db,kind,'committed50')
  assert result and db.is_active
  async with app.sessions() as fresh:
   rows=(await fresh.scalars(select(KafFile))).all()
   assert len(rows)==1 and rows[0].file_id==result['file_id']
  assert len(list(directory.glob('*.kaf')))==1

@pytest.mark.parametrize('fault',['before','rolled_back','mismatch','lookup_failure','driver_disconnect','programmer','cancel'])
async def test_uncertain_is_never_false_success(publisher, monkeypatch, fault):
 app,service,directory=publisher
 async with app.sessions() as db:
  kf=service.publish_from_markdown('# Boundary50','test50')
  commit=db.commit;execute=AsyncSession.execute
  async def lost():
   if fault in {'mismatch','lookup_failure'}:
    await commit()
    if fault=='mismatch':
     async with app.sessions() as fresh:
      await fresh.execute(update(KafFile).where(KafFile.file_id==kf.file_id).values(enabled_tiers='enterprise'));await fresh.commit()
   elif fault=='rolled_back':await db.rollback()
   if fault=='programmer':raise TypeError('programmer50')
   if fault=='cancel':raise asyncio.CancelledError()
   if fault=='driver_disconnect':raise DBAPIError('COMMIT',None,ConnectionError('private-driver-secret50'),connection_invalidated=True)
   raise ConnectionError('private-driver-secret50')
  async def unavailable(session,*args,**kwargs):
   if session is not db:raise ConnectionError('private-lookup-secret50')
   return await execute(session,*args,**kwargs)
  with monkeypatch.context() as patch:
   patch.setattr(db,'commit',lost)
   if fault=='lookup_failure':patch.setattr(AsyncSession,'execute',unavailable)
   if fault in {'programmer','cancel'}:
    with pytest.raises(TypeError if fault=='programmer' else asyncio.CancelledError):await kaf_service.register_publication(db,kf)
   else:
    with pytest.raises(kaf_service.KAFPublicationUncertainError) as caught:await kaf_service.register_publication(db,kf)
    assert caught.value.file_id==kf.file_id and 'secret50' not in str(caught.value)
  assert Path(kf.storage_path).exists()

async def test_attached_update_does_not_recover_as_new_publication(publisher,monkeypatch):
 app,service,_=publisher
 async with app.sessions() as db:
  kf=service.publish_from_markdown('# Attached50','test50');await kaf_service.register_publication(db,kf)
  commit=db.commit;kf.title='updated50'
  async def lost():await commit();raise ConnectionError('lost50')
  monkeypatch.setattr(db,'commit',lost)
  with pytest.raises(kaf_service.KAFPublicationUncertainError):await kaf_service.register_publication(db,kf)

@pytest.mark.parametrize('committed',[True,False])
async def test_real_jwt_admin_observable_result(real_kaf_http,monkeypatch,committed):
 app,client,headers,_,_,_=real_kaf_http
 original=AsyncSession.commit
 async def lost(db):
  if committed:await original(db)
  raise ConnectionError('private-driver-secret50')
 with monkeypatch.context() as patch:
  patch.setattr(AsyncSession,'commit',lost)
  response=await client.post('/api/v1/kaf/admin/publish',headers=headers,json={'markdown':'# Admin50','title':'Admin50'})
 if committed:
  assert response.status_code==201,response.text
  identity=response.json()['file_id']
 else:
  assert response.status_code==503,response.text
  detail=response.json()['detail'];assert detail['code']=='publication_outcome_unknown'
  identity=detail['file_id'];assert detail['status_url']==f'/api/v1/kaf/files/{identity}'
  assert 'secret50' not in response.text and 'retry-after' not in response.headers
 status=await client.get(f'/api/v1/kaf/files/{identity}',headers=headers)
 assert status.status_code==(200 if committed else 404)
 if committed:
  download=await client.get(f'/api/v1/kaf/files/{identity}/download',headers=headers)
  grant=await client.post(f'/api/v1/kaf/files/{identity}/grant',headers=headers)
  assert download.status_code==200 and grant.status_code==200

@pytest.mark.parametrize('expire',[True,False])
async def test_single_slot_pool_and_expiration(publisher,tmp_path,monkeypatch,expire):
 from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
 _,service,_=publisher
 engine=create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "single50.db"}',pool_size=1,max_overflow=0,pool_timeout=.2)
 try:
  async with engine.begin() as connection:await connection.run_sync(KafFile.__table__.create)
  async with async_sessionmaker(engine,expire_on_commit=expire)() as db:
   kf=service.publish_from_markdown('# Single50','test50');identity=kf.file_id;commit=db.commit
   async def lost():await commit();raise ConnectionError('lost50')
   monkeypatch.setattr(db,'commit',lost)
   await asyncio.wait_for(kaf_service.register_publication(db,kf),2)
   assert kf.file_id==identity and kf.title
 finally:await engine.dispose()

async def test_external_transaction_binding_is_not_proof(publisher,monkeypatch):
 app,service,_=publisher
 engine=app.sessions.kw['bind']
 async with engine.connect() as connection:
  async with connection.begin():
   async with AsyncSession(bind=connection,expire_on_commit=False) as db:
    kf=service.publish_from_markdown('# External50','test50');commit=db.commit
    async def lost():await commit();raise ConnectionError('lost50')
    monkeypatch.setattr(db,'commit',lost)
    with pytest.raises(kaf_service.KAFPublicationUncertainError):await kaf_service.register_publication(db,kf)

async def test_native_failed_commit_state_is_retained(publisher,monkeypatch):
 from sqlalchemy.exc import OperationalError
 app,service,_=publisher
 async with app.sessions() as db:
  kf=service.publish_from_markdown('# Failed50','test50');dialect=db.bind.sync_engine.dialect
  def lost(connection):raise OperationalError('COMMIT',None,ConnectionError('private50'),connection_invalidated=True)
  with monkeypatch.context() as patch:
   patch.setattr(dialect,'do_commit',lost)
   with pytest.raises(kaf_service.KAFPublicationUncertainError):await kaf_service.register_publication(db,kf)
   assert not db.is_active and db.in_transaction()
  assert Path(kf.storage_path).exists()
  await db.rollback()
  # SQLite legacy SAVEPOINT release already committed before dialect.do_commit
  # failed. A failed Session is deliberately not enough to infer the outcome.
  assert await db.scalar(select(func.count()).select_from(KafFile))==1

async def test_non_disconnect_dbapi_error_is_not_relabelled(publisher,monkeypatch):
 from sqlalchemy.exc import ProgrammingError
 app,service,_=publisher
 async with app.sessions() as db:
  kf=service.publish_from_markdown('# Programming50','test50')
  async def bad():raise ProgrammingError('COMMIT',None,ValueError('programmer50'))
  monkeypatch.setattr(db,'commit',bad)
  with pytest.raises(ProgrammingError):await kaf_service.register_publication(db,kf)

async def test_recovery_keeps_loaded_caller_document(publisher,monkeypatch):
 from app.models import Document
 from sqlalchemy import inspect
 app,_,_=publisher
 async with app.sessions() as db:
  async with db.bind.begin() as connection:await connection.run_sync(Document.__table__.create)
  doc=Document(filename='original50.md',file_type='md',sha256='a'*64)
  db.add(doc);await db.commit();identity=doc.id;commit=db.commit
  async def lost():await commit();raise ConnectionError('lost50')
  monkeypatch.setattr(db,'commit',lost)
  assert await publish(db,'document',identity)
  assert not inspect(doc).expired_attributes and doc.id==identity and doc.filename=='original50.md'
