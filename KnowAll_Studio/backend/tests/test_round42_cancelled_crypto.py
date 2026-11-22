"""Independent candidate: cancellation must not produce a late DB write."""
import asyncio
import threading

import pytest
from filelock import FileLock
from app.core import crypto
from app.core.mcp import store
from tests.test_product_auth import account_app,register,bearer
from tests.test_round39_mcp_persistence import private_app,SERVER


async def test_actual_cancelled_mcp_crypto_has_no_late_db_write(private_app,monkeypatch):
    owner=await register(private_app);key=crypto._KEY_FILE;key.unlink()
    entered=threading.Event();finished=threading.Event()
    original=store.encrypt_api_key
    def observed(value):
        entered.set()
        try:return original(value)
        finally:finished.set()
    monkeypatch.setattr(store,'encrypt_api_key',observed)
    lock=FileLock(str(key)+'.lock');lock.acquire(timeout=1)
    task=asyncio.create_task(private_app.client.post('/mcp/servers',headers=bearer(owner['access_token']),json=SERVER))
    try:
        async with asyncio.timeout(3):
            while not entered.is_set():await asyncio.sleep(.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        lock.release()
        async with asyncio.timeout(3):
            while not finished.is_set():await asyncio.sleep(.005)
        listed=await private_app.client.get('/mcp/servers',headers=bearer(owner['access_token']))
        assert listed.status_code==200 and listed.json()['servers']==[]
        saved=await private_app.client.post('/mcp/servers',headers=bearer(owner['access_token']),json=SERVER)
        assert saved.status_code==200
        assert len((await private_app.client.get('/mcp/servers',headers=bearer(owner['access_token']))).json()['servers'])==1
    finally:
        lock.release()
        if not task.done():task.cancel()
