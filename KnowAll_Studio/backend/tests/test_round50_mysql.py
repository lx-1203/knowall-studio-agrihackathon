"""Real MySQL/asyncmy commit with response-boundary injection, not packet loss."""
from pathlib import Path
import pytest
from sqlalchemy import select
from tests.test_round47_mysql import mysql47,private_kaf
from tests.test_round44_auto_publish import publish
from app.models.kaf import KafFile

@pytest.mark.parametrize('kind',['document','summary'])
async def test_actual_mysql_commit_response_boundary(mysql47,monkeypatch,kind):
 _,sessions,_,directory,_=mysql47
 async with sessions() as db:
  original=db.commit
  async def lost():await original();raise ConnectionError('lost-response50')
  monkeypatch.setattr(db,'commit',lost)
  result=await publish(db,kind,'mysql-committed50')
  assert result
 async with sessions() as db:
  rows=(await db.scalars(select(KafFile))).all()
  assert len(rows)==1 and rows[0].file_id==result['file_id']
  assert Path(rows[0].storage_path).exists()
  assert await publish(db,kind,'mysql-committed50') is None
 assert len(list(directory.glob('*.kaf')))==1

async def test_driver_disconnect_during_mysql_commit_stays_uncertain(mysql47,monkeypatch):
 from asyncmy.errors import OperationalError
 from sqlalchemy import func
 from app.core import kaf_service
 engine,sessions,service,directory,_=mysql47
 calls=[]
 async with sessions() as db:
  kf=service.publish_from_markdown('# Driver50','test50');identity=kf.file_id
  def disconnect(connection):
   calls.append(1)
   # Actual driver exception at the dialect boundary: SQLAlchemy classifies
   # error 2013 and invalidates the real native connection. No network proxy.
   raise OperationalError(2013,'Lost connection to MySQL server during query')
  with monkeypatch.context() as patch:
   patch.setattr(engine.sync_engine.dialect,'do_commit',disconnect)
   with pytest.raises(kaf_service.KAFPublicationUncertainError) as caught:
    await kaf_service.register_publication(db,kf)
   assert caught.value.file_id==identity and caught.value.__cause__.connection_invalidated
   assert not db.is_active and db.in_transaction() and calls==[1]
  await db.rollback()
 async with sessions() as fresh:
  assert await fresh.scalar(select(func.count()).select_from(KafFile))==0
 assert len(list(directory.glob('*.kaf')))==1
