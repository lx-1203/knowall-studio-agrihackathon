"""Real MySQL and separate OS processes; no shared file locks or mocked DB."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import pytest
import pymysql
from cryptography.hazmat.primitives import serialization
from sqlalchemy import select, func, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core import kaf_service, kaf_publisher
from app.models.kaf import KafFile
from kaf.format import unpack_kaf
from tests.test_round41_key_recovery import private_kaf


@pytest.fixture
async def mysql47(private_kaf, tmp_path, monkeypatch):
    url = os.environ['KNOWALL_ROUND47_MYSQL_URL']
    assert 'round47_private' in url and '127.0.0.1' in url
    engine = create_async_engine(url, pool_size=4, max_overflow=0, pool_timeout=.3)
    async with engine.begin() as conn:
        await conn.run_sync(KafFile.__table__.create)
    service = kaf_service.KAFService(); service.public_key
    monkeypatch.setattr(kaf_publisher, 'get_kaf_service', lambda: service)
    directory = tmp_path/'publications'
    monkeypatch.setattr(kaf_service, 'KAF_DIR', directory)
    try:
        yield engine, async_sessionmaker(engine, expire_on_commit=False), service, directory, url
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(KafFile.__table__.drop)
        await engine.dispose()


@pytest.mark.parametrize('kind', ['document', 'summary'])
async def test_separate_processes_and_directories_publish_source_once(mysql47, tmp_path, kind):
    engine, sessions, service, directory, url = mysql47
    key = tmp_path/'private-test-key.pem'
    key.write_bytes(service.private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    source = 'separate47-'+kind
    gate = 'test47-build-'+kind
    a, b = tmp_path/'worker-a', tmp_path/'worker-b'
    parsed = make_url(url)
    def compete():
        children = []
        with pymysql.connect(host=parsed.host, port=parsed.port, user=parsed.username, password=parsed.password, database=parsed.database, autocommit=True) as controller:
            with controller.cursor() as cursor:
                cursor.execute('SELECT GET_LOCK(%s,0)', (gate,)); assert cursor.fetchone()[0] == 1
                try:
                    args = [sys.executable, str(Path(__file__).with_name('round47_mysql_worker.py')), url]
                    for folder in [a,b]:
                        children.append(subprocess.Popen([*args,str(folder),str(key),source,kind,gate],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=subprocess.CREATE_NO_WINDOW))
                        deadline=time.monotonic()+15
                        while not (folder/'building').exists():
                            # B either reaches the old builder or waits on the real production GET_LOCK.
                            cursor.execute('SHOW PROCESSLIST')
                            waiting = any(row[6]=='User lock' and row[7] and 'kaf:' in row[7] for row in cursor.fetchall())
                            if folder == b and waiting: break
                            assert time.monotonic()<deadline, 'Worker neither built nor waited on the native publication lock'
                            time.sleep(.03)  # Test synchronization, not round work padding.
                finally:
                    cursor.execute('SELECT RELEASE_LOCK(%s)', (gate,))
                results=[]
                try:
                    for child in children:
                        stdout, stderr=child.communicate(timeout=20)
                        assert child.returncode==0, stderr
                        results.append(json.loads(stdout))
                    assert results[0]['pid'] != results[1]['pid']
                    return results
                finally:
                    for child in children:
                        if child.poll() is None: child.terminate(); child.wait(timeout=5)
    results = await asyncio.to_thread(compete)
    assert sum(item['result'] is not None for item in results)==1, results
    async with sessions() as db:
        rows=(await db.scalars(select(KafFile))).all()
        assert len(rows)==1
        assert unpack_kaf(Path(rows[0].storage_path).read_bytes(),service.private_key)['checksum_ok']
    assert len(list(a.glob('*.kaf')))+len(list(b.glob('*.kaf')))==1


async def test_pool_one_skips_before_build_and_pool_two_recovers(mysql47):
    _, _, service, directory, url = mysql47
    for capacity in [1,2]:
        engine=create_async_engine(url,pool_size=capacity,max_overflow=0,pool_timeout=.03)
        try:
            async with async_sessionmaker(engine,expire_on_commit=False)() as db:
                result=await kaf_publisher.publish_document(db,'pool47','pool.md',markdown='# Pool47')
                if capacity == 1:
                    assert result is None and not list(directory.glob('*.kaf'))
                    assert await db.scalar(select(func.count()).select_from(KafFile))==0
                else: assert result
        finally: await engine.dispose()


async def test_native_lock_timeout_preserves_other_owner_and_can_retry(mysql47, monkeypatch):
    engine,sessions,service,directory,url=mysql47
    source='timeout47'
    async with engine.connect() as owner:
        database=await owner.scalar(select(func.database()))
        name=kaf_publisher._mysql_publication_lock_name(database,source)
        assert len(name)==64
        assert name!=kaf_publisher._mysql_publication_lock_name(database+'-other',source)
        assert await owner.scalar(select(func.get_lock(name,0)))==1
        owner_id=await owner.scalar(select(func.connection_id()))
        monkeypatch.setattr(kaf_publisher,'MYSQL_PUBLICATION_LOCK_TIMEOUT',0)
        async with sessions() as db:
            assert await kaf_publisher.publish_document(db,source,'timeout.md',markdown='# Timeout') is None
            assert not list(directory.glob('*.kaf'))
        assert await owner.scalar(select(func.is_used_lock(name)))==owner_id
        assert await owner.scalar(select(func.release_lock(name)))==1
    async with sessions() as db:
        assert await kaf_publisher.publish_document(db,source,'timeout.md',markdown='# Retry')
    async with engine.connect() as conn:
        assert await conn.scalar(select(func.is_used_lock(name))) is None


async def test_cancel_waiting_on_native_lock_releases_connection_and_never_builds(mysql47):
    engine,sessions,service,directory,url=mysql47
    async with engine.connect() as owner, sessions() as db:
        name=kaf_publisher._mysql_publication_lock_name(await owner.scalar(select(func.database())),'cancel47')
        assert await owner.scalar(select(func.get_lock(name,0)))==1
        task=asyncio.create_task(kaf_publisher.publish_document(db,'cancel47','cancel.md',markdown='# Cancel'))
        try:
            deadline=time.monotonic()+4
            while True:
                rows=(await owner.execute(text('SHOW PROCESSLIST'))).all()
                if any(row[6]=='User lock' and row[7] and name in row[7] for row in rows): break
                assert time.monotonic()<deadline
                await asyncio.sleep(.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            assert not list(directory.glob('*.kaf'))
        finally:
            await owner.scalar(select(func.release_lock(name)))
            if not task.done(): task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        assert await kaf_publisher.publish_document(db,'cancel47','retry.md',markdown='# After cancel')
    async with engine.connect() as conn:
        assert await conn.scalar(select(func.is_used_lock(name))) is None


@pytest.mark.parametrize('fault',['build','flush','precommit','postcommit'])
async def test_failure_releases_native_lock_and_retains_unknown_commit_files(mysql47, monkeypatch, fault):
    from sqlalchemy.exc import IntegrityError
    engine,sessions,service,directory,url=mysql47
    source='fault47-'+fault
    original=service.publish_from_markdown
    def build(*args,**kwargs):
        if fault=='build': raise ValueError('synthetic-build47')
        row=original(*args,**kwargs)
        if fault=='flush': row.title=None
        return row
    async with sessions() as db:
        commit=db.commit
        async def lost():
            if fault=='postcommit': await commit()
            raise ConnectionError('synthetic-commit47')
        with monkeypatch.context() as patch:
            patch.setattr(service,'publish_from_markdown',build)
            if fault.endswith('commit'): patch.setattr(db,'commit',lost)
            if fault=='build': assert await kaf_publisher.publish_document(db,source,'fault.md',markdown='# Fault') is None
            else:
                with pytest.raises(IntegrityError if fault=='flush' else ConnectionError):
                    await kaf_publisher.publish_document(db,source,'fault.md',markdown='# Fault')
        await db.rollback()
    async with engine.connect() as conn:
        name=kaf_publisher._mysql_publication_lock_name(await conn.scalar(select(func.database())),source)
        assert await conn.scalar(select(func.is_used_lock(name))) is None
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(KafFile))==(1 if fault=='postcommit' else 0)
        files=list(directory.glob('*.kaf'))
        assert len(files)==(1 if fault.endswith('commit') else 0)
        for path in files: assert unpack_kaf(path.read_bytes(),service.private_key)['checksum_ok']
        result=await kaf_publisher.publish_document(db,source,'retry.md',markdown='# Retry')
        assert (result is None)==(fault=='postcommit')


async def test_lost_lock_connection_during_build_never_registers_file(mysql47, monkeypatch):
    from sqlalchemy.exc import DBAPIError
    engine,sessions,service,directory,url=mysql47
    parsed=make_url(url); source='lost-session47'
    name=kaf_publisher._mysql_publication_lock_name(parsed.database,source)
    original=service.publish_from_markdown
    def lose_session(*args,**kwargs):
        with pymysql.connect(host=parsed.host,port=parsed.port,user=parsed.username,password=parsed.password,database=parsed.database,autocommit=True) as controller:
            with controller.cursor() as cursor:
                cursor.execute('SELECT IS_USED_LOCK(%s)',(name,))
                owner_id=cursor.fetchone()[0]
                assert isinstance(owner_id,int)  # Only this private source's actual owner.
                cursor.execute('KILL CONNECTION '+str(owner_id))
        return original(*args,**kwargs)
    async with sessions() as db:
        with monkeypatch.context() as patch:
            patch.setattr(service,'publish_from_markdown',lose_session)
            with pytest.raises(DBAPIError):
                await kaf_publisher.publish_document(db,source,'lost.md',markdown='# Lost session')
        assert not list(directory.glob('*.kaf'))
        assert await db.scalar(select(func.count()).select_from(KafFile))==0
        assert await kaf_publisher.publish_document(db,source,'recovered.md',markdown='# Recovered')
