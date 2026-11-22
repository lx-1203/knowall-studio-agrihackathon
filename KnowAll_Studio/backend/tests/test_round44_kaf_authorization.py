"""Authorized/denied requests during real private-key outages, temporary SQLite/JWT."""
from pathlib import Path
import pytest
from sqlalchemy import select,func
from app.core import crypto,kaf_service
from app.models.kaf import KafFile,KafGrant
from app.models.user import User
from tests.test_round43_kaf_recovery import real_kaf_http,account_app,private_kaf

@pytest.mark.parametrize('fault',['missing_fernet','bad_public'])
@pytest.mark.parametrize('denial,status',[('unknown',404),('unpublished',404),('tier',403),('revoked',403)])
async def test_denied_grant_ignores_key_outage_and_has_no_crypto_side_effects(real_kaf_http,fault,denial,status,monkeypatch):
 app,client,headers,doc,expected,service=real_kaf_http
 async with app.sessions() as db:
  saved=await db.scalar(select(KafFile).where(KafFile.file_id==doc.file_id))
  user=await db.scalar(select(User))
  if denial=='unpublished':saved.is_published=False
  if denial=='tier':saved.enabled_tiers='enterprise'
  if denial=='revoked':db.add(KafGrant(file_id=doc.file_id,user_id=user.id,revoked=True))
  await db.commit()
 original_key=crypto._KEY_FILE.read_bytes();original_pub=kaf_service._PUB_KEY_FILE.read_bytes()
 if fault=='missing_fernet':crypto._KEY_FILE.unlink()
 else:kaf_service._PUB_KEY_FILE.write_bytes(b'bad44-public')
 attempts=[];original=service._load_or_create_keypair
 def observed():attempts.append(True);return original()
 monkeypatch.setattr(service,'_load_or_create_keypair',observed)
 before={p:p.read_bytes() for p in kaf_service._KEY_DIR.iterdir() if p.is_file()}
 fid='unknown44' if denial=='unknown' else doc.file_id
 response=await client.post(f'/api/v1/kaf/files/{fid}/grant',headers=headers)
 assert response.status_code==status,response.text
 assert 'retry-after' not in response.headers and 'content_key_hex' not in response.text
 assert attempts==[],'Denied request touched account keys'
 assert before=={p:p.read_bytes() for p in kaf_service._KEY_DIR.iterdir() if p.is_file()}
 if fault=='missing_fernet':assert not crypto._KEY_FILE.exists()
 assert service._private_key is None and service._grant_cache=={}
 async with app.sessions() as db:
  assert await db.scalar(select(func.count()).select_from(KafGrant))==(1 if denial=='revoked' else 0)
  saved=await db.scalar(select(KafFile).where(KafFile.file_id==doc.file_id))
  saved.is_published=True;saved.enabled_tiers='free,pro,enterprise'
  if denial=='revoked':await service.restore(db,doc.file_id)
  await db.commit()
 permitted=await client.post(f'/api/v1/kaf/files/{doc.file_id}/grant',headers=headers)
 assert permitted.status_code==503 and permitted.headers['retry-after']=='5'
 crypto._KEY_FILE.write_bytes(original_key);kaf_service._PUB_KEY_FILE.write_bytes(original_pub)
 recovered=await client.post(f'/api/v1/kaf/files/{doc.file_id}/grant',headers=headers)
 assert recovered.status_code==200 and recovered.json()['content_key_hex']==expected

async def test_unknown_first_grant_does_not_create_any_account_key(real_kaf_http):
 _,client,headers,doc,_,service=real_kaf_http
 for p in kaf_service._KEY_DIR.iterdir():p.unlink()
 kaf_service._KEY_DIR.rmdir();crypto._KEY_FILE.unlink()
 response=await client.post('/api/v1/kaf/files/cold-unknown44/grant',headers=headers)
 assert response.status_code==404,response.text
 assert not kaf_service._KEY_DIR.exists() and not crypto._KEY_FILE.exists()
 assert service._private_key is None
