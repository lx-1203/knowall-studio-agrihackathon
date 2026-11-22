"""Cold secret races use real spawned processes and private temporary files."""
import base64
import hashlib
import multiprocessing as mp
import os
from pathlib import Path
import time

import pytest
from cryptography.fernet import Fernet


def _worker_env(key_file):
    os.environ['KNOWALL_IGNORE_ENV_FILE']='true'
    os.environ['KNOWALL_DATA_ROOT']=str(Path(key_file).parent)
    os.environ['KNOWALL_SECRET_KEY_FILE']=str(key_file)
    for name in list(os.environ):
        if name.endswith(('API_KEY','AUTH_TOKEN')): os.environ.pop(name)


def cold_worker(key_file, start, race, results):
    _worker_env(key_file)
    from app.core import crypto
    crypto._KEY_FILE=Path(key_file)
    original=Path.exists
    gated=False
    def checked_missing(path):
        nonlocal gated
        exists=original(path)
        if path==crypto._KEY_FILE and not exists and not gated:
            gated=True
            race.wait(timeout=12)  # Widen the existing check/write race, not a new algorithm.
        return exists
    Path.exists=checked_missing
    try:
        start.wait(timeout=12)
        key=crypto._get_or_create_key()
        results.put({'pid':os.getpid(),'key_hash':hashlib.sha256(key).hexdigest(),
                     'cipher':Fernet(key).encrypt(b'synthetic-round40').decode()})
    except Exception as exc:
        results.put({'pid':os.getpid(),'error':type(exc).__name__})


def test_four_real_processes_publish_one_complete_cold_key(tmp_path):
    ctx=mp.get_context('spawn'); start=ctx.Barrier(4); race=ctx.Barrier(4); results=ctx.Queue()
    key=tmp_path/'cold'/'key'; key.parent.mkdir()
    children=[ctx.Process(target=cold_worker,args=(str(key),start,race,results)) for _ in range(4)]
    try:
        for child in children: child.start()
        records=[results.get(timeout=25) for _ in children]
        assert all('error' not in record for record in records), records
        assert len({record['pid'] for record in records})==4
        assert len({record['key_hash'] for record in records})==1, records
        final=Fernet(key.read_bytes())
        assert all(final.decrypt(record['cipher'].encode())==b'synthetic-round40' for record in records)
    finally:
        for child in children:
            child.join(timeout=5)
            if child.is_alive(): child.kill(); child.join(timeout=5)
        results.close(); results.join_thread()


def test_failed_publication_never_leaves_partial_target(tmp_path,monkeypatch):
    from app.core import crypto
    key=tmp_path/'key'; monkeypatch.setattr(crypto,'_KEY_FILE',key)
    original=Path.write_bytes
    def legacy_partial(path,data):
        if path==key:
            original(path,data[:8]); raise OSError('synthetic disk full')
        return original(path,data)
    monkeypatch.setattr(Path,'write_bytes',legacy_partial)
    def failed_replace(*args): raise OSError('synthetic disk full')
    monkeypatch.setattr(os,'replace',failed_replace)
    with pytest.raises(RuntimeError): crypto._get_or_create_key()
    assert not key.exists()
    assert not list(tmp_path.glob('*.tmp'))


def test_existing_invalid_key_is_rejected_without_replacement(tmp_path,monkeypatch):
    from app.core import crypto
    key=tmp_path/'key'; key.write_bytes(b'old-invalid-key')
    monkeypatch.setattr(crypto,'_KEY_FILE',key)
    with pytest.raises(RuntimeError): crypto._get_or_create_key()
    assert key.read_bytes()==b'old-invalid-key'


def test_existing_key_and_both_cipher_formats_keep_working(tmp_path,monkeypatch):
    from app.core import crypto
    key=tmp_path/'key'; original=Fernet.generate_key(); key.write_bytes(original)
    monkeypatch.setattr(crypto,'_KEY_FILE',key)
    token=Fernet(original).encrypt('synthetic旧密文'.encode())
    assert crypto.decrypt_api_key(token.decode())=='synthetic旧密文'
    assert crypto.decrypt_api_key(base64.b64encode(token).decode())=='synthetic旧密文'
    assert crypto.get_fernet().decrypt(crypto.encrypt_api_key('new').encode())==b'new'
    assert key.read_bytes()==original


def held_lock_worker(key_file,ready,release):
    from filelock import FileLock
    with FileLock(key_file+'.lock'):
        ready.set(); release.wait(timeout=20)


def test_lock_timeout_then_process_crash_releases_native_lock(tmp_path,monkeypatch):
    from app.core import crypto,secret_file
    ctx=mp.get_context('spawn'); ready=ctx.Event(); release=ctx.Event(); key=tmp_path/'key'
    child=ctx.Process(target=held_lock_worker,args=(str(key),ready,release))
    monkeypatch.setattr(crypto,'_KEY_FILE',key); monkeypatch.setattr(secret_file,'_LOCK_TIMEOUT',.15)
    try:
        child.start(); assert ready.wait(timeout=10)
        started=time.monotonic()
        with pytest.raises(RuntimeError): crypto._get_or_create_key()
        assert time.monotonic()-started<2 and not key.exists()
        child.kill(); child.join(timeout=5)
        assert not child.is_alive()
        assert crypto.get_fernet().decrypt(crypto.encrypt_api_key('recovered').encode())==b'recovered'
    finally:
        # A killed Event.wait owner can leave Event's condition corrupted.
        # Do not reuse its synchronization primitive after forceful termination.
        if child.is_alive(): child.kill(); child.join(timeout=5)


def test_existing_key_read_does_not_wait_for_creation_lock(tmp_path,monkeypatch):
    from app.core import crypto
    from filelock import FileLock
    key=tmp_path/'key'; original=Fernet.generate_key(); key.write_bytes(original)
    monkeypatch.setattr(crypto,'_KEY_FILE',key)
    with FileLock(str(key)+'.lock'):
        assert crypto._get_or_create_key()==original


def interrupted_publisher(key_file,ready,release):
    _worker_env(key_file)
    from app.core import crypto
    crypto._KEY_FILE=Path(key_file)
    original=os.replace
    def before_publication(*args):
        ready.set(); release.wait(timeout=20)
        return original(*args)
    os.replace=before_publication
    crypto._get_or_create_key()


def test_crash_before_atomic_publish_keeps_target_absent_and_next_creation_recovers(tmp_path,monkeypatch):
    from app.core import crypto
    ctx=mp.get_context('spawn'); ready=ctx.Event(); release=ctx.Event(); key=tmp_path/'key'
    child=ctx.Process(target=interrupted_publisher,args=(str(key),ready,release))
    monkeypatch.setattr(crypto,'_KEY_FILE',key)
    try:
        child.start(); assert ready.wait(timeout=10)
        assert not key.exists()
        child.kill(); child.join(timeout=5)
        assert not child.is_alive()
        abandoned=list(tmp_path.glob('*.tmp'))
        assert len(abandoned)==1  # Never silently delete another process's secret file.
        key_bytes=crypto._get_or_create_key()
        assert Fernet(key_bytes).decrypt(crypto.encrypt_api_key('next-start').encode())==b'next-start'
        assert abandoned[0].exists()  # Private crash debris is an explicit limitation.
    finally:
        if child.is_alive(): child.kill(); child.join(timeout=5)


def test_unreadable_existing_path_is_not_replaced(tmp_path,monkeypatch):
    from app.core import crypto
    key=tmp_path/'key'; key.mkdir()
    monkeypatch.setattr(crypto,'_KEY_FILE',key)
    with pytest.raises(RuntimeError): crypto._get_or_create_key()
    assert key.is_dir()
