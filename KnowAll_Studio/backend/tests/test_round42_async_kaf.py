"""Actual SQLite/JWT KAF grant keeps crypto and file waits off its loop."""
import asyncio
import threading

import pytest
from app.core import kaf_service
from app.models.kaf import KafFile,KafGrant
from app.models.subscription import UserTier
from tests.test_product_auth import account_app,register,bearer,load_account_router
from tests.test_round41_key_recovery import private_kaf


@pytest.mark.parametrize('phase',['load','unwrap','publish'])
async def test_actual_grant_crypto_thread_and_sqlite_audit(account_app,private_kaf,tmp_path,monkeypatch,phase):
    monkeypatch.setattr(kaf_service,'KAF_DIR',tmp_path/'files')
    owner=await register(account_app)
    service=kaf_service.KAFService()
    file=service.publish_from_markdown('# Async42\n\nreal encrypted lesson','test42')
    expected=service._unwrap_content_key(__import__('pathlib').Path(file.storage_path).read_bytes()).hex()
    async with account_app.sessions() as db:
        async with db.bind.begin() as c:
            for table in [KafFile.__table__,KafGrant.__table__,UserTier.__table__]:
                await c.run_sync(table.create)
        db.add(file);await db.commit()
        if phase=='publish':
            from app.models.user import User
            user=await db.get(User,owner['user']['id']);user.is_admin=True;await db.commit()
    fresh=kaf_service.KAFService()
    router=load_account_router('kaf')
    monkeypatch.setattr(router,'get_kaf_service',lambda:fresh)
    account_app.app.include_router(router.router)
    entered=threading.Event(); release=threading.Event(); threads=[]
    method='_unwrap_content_key' if phase=='unwrap' else '_load_or_create_keypair'
    original=getattr(fresh,method)
    def observed(*args):
        threads.append(threading.get_ident());entered.set();release.wait(.8)
        return original(*args)
    monkeypatch.setattr(fresh,method,observed)
    task=asyncio.create_task(account_app.client.post('/api/v1/kaf/admin/publish' if phase=='publish'
        else f'/api/v1/kaf/files/{file.file_id}/grant',headers=bearer(owner['access_token']),
        **({'json':{'markdown':'# Publish42\n\nactual lesson','title':'Publish42','topic':'test42'}} if phase=='publish' else {})))
    try:
        async with asyncio.timeout(4):
            while not entered.is_set(): await asyncio.sleep(.005)
        assert threads[0]!=threading.get_ident(), 'Actual grant crypto blocked the request loop'
        assert not task.done()
        release.set();response=await task
        if phase=='publish':
            assert response.status_code==201
            async with account_app.sessions() as db:
                from sqlalchemy import select
                saved=await db.scalar(select(KafFile).where(KafFile.file_id==response.json()['file_id']))
                assert saved.title=='Publish42'
            return
        assert response.status_code==200 and response.json()['content_key_hex']==expected
        async with account_app.sessions() as db:
            from sqlalchemy import select
            audit=(await db.scalars(select(KafGrant))).one()
            assert audit.user_id==owner['user']['id'] and audit.file_id==file.file_id
        again=await account_app.client.post(f'/api/v1/kaf/files/{file.file_id}/grant',headers=bearer(owner['access_token']))
        assert again.status_code==200 and again.json()['cached'] is True
    finally:
        release.set()
        if not task.done(): await task

