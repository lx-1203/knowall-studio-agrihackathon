"""Use a real native cross-process key lock and real ASGI calls to prove progress."""
import asyncio
import multiprocessing as mp
import threading
import time

import pytest
from filelock import FileLock
from app.core import crypto, secret_file
from tests.test_product_auth import account_app, register, bearer
from tests.test_round39_mcp_persistence import private_app, SERVER
from tests.test_product_auth import load_account_router


def hold_creation_lock(path,ready,release):
    with FileLock(path+'.lock'):
        ready.set(); release.wait(timeout=12)


async def test_real_mcp_cold_lock_wait_keeps_same_loop_ping_responsive(private_app,monkeypatch):
    user=await register(private_app); key=crypto._KEY_FILE; key.unlink()
    ctx=mp.get_context('spawn'); ready=ctx.Event(); release=ctx.Event()
    child=ctx.Process(target=hold_creation_lock,args=(str(key),ready,release))
    attempted=threading.Event(); timestamps=[]; post=None
    original=FileLock._acquire
    def observed_acquire(lock):
        if lock.lock_file==str(key)+'.lock' and not attempted.is_set():
            timestamps.append(time.monotonic()); attempted.set()
        return original(lock)
    monkeypatch.setattr(FileLock,'_acquire',observed_acquire)
    monkeypatch.setattr(secret_file,'_LOCK_TIMEOUT',.8)
    @private_app.app.get('/round41/ping')
    async def ping(): return {'ok':True}
    try:
        child.start(); assert ready.wait(timeout=8)
        post=asyncio.create_task(private_app.client.post('/mcp/servers',headers=bearer(user['access_token']),json=SERVER))
        async with asyncio.timeout(3):
            while not attempted.is_set(): await asyncio.sleep(.005)
        response=await private_app.client.get('/round41/ping')
        assert response.status_code==200
        assert time.monotonic()-timestamps[0]<.35, 'Native lock blocked unrelated same-loop HTTP'
        assert not post.done(), 'Lock-holder is still live; crypto write must be waiting'
        release.set(); child.join(timeout=5)
        saved=await post
        assert saved.status_code==200
        assert (await private_app.client.get('/mcp/servers',headers=bearer(user['access_token']))).status_code==200
    finally:
        release.set()
        if child.pid is not None:
            child.join(timeout=5)
            if child.is_alive(): child.kill(); child.join(timeout=5)
        if post is not None:
            try: await post
            except Exception: pass


@pytest.mark.parametrize('entry', ['user', 'admin', 'auth'])
async def test_other_real_crypto_consumers_leave_async_loop_and_session_on_original_thread(account_app,tmp_path,monkeypatch,entry):
    from app.models import APIKey
    from app.core import auth
    from app.core.api_scheduler import api_client, get_current_user_id
    from tests.test_product_auth import user_api
    admin=load_account_router('admin')
    account_app.app.include_router(admin.router)
    async with account_app.sessions() as db:
        async with db.bind.begin() as connection:
            await connection.run_sync(APIKey.__table__.create)
    user=await register(account_app)
    owner=user['user']['id']; main_thread=threading.get_ident()
    monkeypatch.setattr(crypto,'_KEY_FILE',tmp_path/'secret')
    token=crypto.encrypt_api_key('synthetic41-consumer')
    calls=[]
    monkeypatch.setattr(api_client,'configure_adapter',lambda *args,**kwargs:calls.append((args,kwargs)))
    if entry=='auth':
        async with account_app.sessions() as db:
            db.add(APIKey(user_id=owner,provider='openai',key_encrypted=token))
            await db.commit()
    entered=threading.Event(); release=threading.Event(); worker_threads=[]
    original=crypto.decrypt_api_key if entry=='auth' else crypto.encrypt_api_key
    def observed(value):
        worker_threads.append(threading.get_ident()); entered.set()
        assert release.wait(timeout=2)
        return original(value)
    if entry=='auth': monkeypatch.setattr(crypto,'decrypt_api_key',observed)
    else: monkeypatch.setattr(user_api if entry=='user' else admin,'encrypt_api_key',observed)
    async def operation():
        if entry=='auth':
            async with account_app.sessions() as db:
                # Execute/commit are real async operations, never passed to worker.
                await auth.load_user_api_keys(owner,db)
                return get_current_user_id()
        return await account_app.client.post('/api/v1/user/keys' if entry=='user' else '/api/v1/admin/keys',
            headers=bearer(user['access_token']),json={'provider':'openai','api_key':'synthetic41-consumer'})
    task=asyncio.create_task(operation())
    try:
        async with asyncio.timeout(3):
            while not entered.is_set(): await asyncio.sleep(.005)
        assert worker_threads==[worker_threads[0]] and worker_threads[0]!=main_thread
        assert not task.done()
        release.set(); result=await task
        if entry=='auth':
            assert result==owner and calls and all(args[1]=='synthetic41-consumer' for args,_ in calls)
        else:
            assert result.status_code==(201 if entry=='user' else 200), result.text
            async with account_app.sessions() as db:
                from sqlalchemy import select
                saved=(await db.scalars(select(APIKey))).one()
                assert crypto.decrypt_api_key(saved.key_encrypted)=='synthetic41-consumer'
    finally:
        release.set()
        try: await task
        except Exception: pass
