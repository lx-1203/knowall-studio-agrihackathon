"""Real private MySQL 8 / production asyncmy driver, native RSA and publisher."""
import os
import asyncio
import threading
import uuid
import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from kaf.format import unpack_kaf
from app.core import kaf_service, kaf_publisher
from app.models.kaf import KafFile
from tests.test_round41_key_recovery import private_kaf
from tests.test_round46_source_migration import legacy, migration, row


@pytest.fixture
async def mysql_publisher(private_kaf, tmp_path, monkeypatch):
    # URL is supplied only by the isolated round46 process harness.
    url = os.environ['KNOWALL_ROUND46_MYSQL_URL']
    assert 'round46_private' in url and '127.0.0.1' in url
    engine = create_async_engine(url, isolation_level='REPEATABLE READ')
    async with engine.begin() as connection:
        await connection.run_sync(KafFile.__table__.create)
    service = kaf_service.KAFService(); service.public_key
    monkeypatch.setattr(kaf_publisher, 'get_kaf_service', lambda: service)
    directory = tmp_path/'publications'
    monkeypatch.setattr(kaf_service, 'KAF_DIR', directory)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False), service, directory
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(KafFile.__table__.drop)
        await engine.dispose()


async def publish(db, kind, source):
    if kind == 'document':
        return await kaf_publisher.publish_document(db, source, 'mysql46.md', markdown='# Real MySQL46\n\nNative content')
    return await kaf_publisher.publish_summary_package(db, source, 'Summary46', 'Cloud46', [{'title':'Native46', 'body':'Native content'}])


@pytest.mark.parametrize('kind', ['document', 'summary'])
async def test_uuid_source_roundtrips_strict_mysql(mysql_publisher, kind):
    sessions, service, directory = mysql_publisher
    source = str(uuid.uuid4())
    async with sessions() as db:
        result = await publish(db, kind, source)
        assert result
    async with sessions() as db:
        row = (await db.scalars(select(KafFile))).one()
        assert row.source_doc_id == (source if kind == 'document' else 'summary:'+source)
        assert unpack_kaf((directory/(row.file_id+'.kaf')).read_bytes(), service.private_key)['checksum_ok']
        assert await publish(db, kind, source) is None


@pytest.mark.parametrize('kind', ['document', 'summary'])
async def test_old_repeatable_read_snapshot_does_not_duplicate(mysql_publisher, kind):
    sessions, service, directory = mysql_publisher
    source = 'snapshot46'
    async with sessions() as old, sessions() as writer:
        assert await old.scalar(select(func.count()).select_from(KafFile)) == 0
        assert await publish(writer, kind, source)
        # Prove this is a real old MySQL consistent snapshot, not a mock session.
        assert await old.scalar(select(func.count()).select_from(KafFile)) == 0
        assert await publish(old, kind, source) is None, 'Lock holder used an old consistent-read snapshot'
        await old.rollback()
    async with sessions() as db:
        assert await db.scalar(select(func.count()).select_from(KafFile)) == 1
        row = (await db.scalars(select(KafFile))).one()
        assert unpack_kaf((directory/(row.file_id+'.kaf')).read_bytes(), service.private_key)['checksum_ok']
    assert len(list(directory.glob('*.kaf'))) == 1


async def test_different_sources_remain_parallel_mysql(mysql_publisher, monkeypatch):
    sessions, service, directory = mysql_publisher
    original = service.publish_from_markdown
    barrier = threading.Barrier(2)
    def concurrent(*args, **kwargs):
        barrier.wait(timeout=5)
        return original(*args, **kwargs)
    monkeypatch.setattr(service, 'publish_from_markdown', concurrent)
    async def request(source):
        async with sessions() as db: return await publish(db, 'document', source)
    results = await asyncio.gather(request('parallel-a46'), request('parallel-z46'))
    assert all(results) and results[0]['file_id'] != results[1]['file_id']
    assert len(list(directory.glob('*.kaf'))) == 2


async def test_real_mysql_legacy_migration_and_guard(tmp_path):
    engine = create_async_engine(os.environ['KNOWALL_ROUND46_MYSQL_URL'])
    try:
        async with engine.begin() as connection:
            def upgrade(sync):
                import sqlalchemy as sa
                table = legacy(sync)
                sync.execute(table.insert(), [row('old1','same46'), row('old2','same46'), row('null',None)])
                before = sync.execute(sa.select(table).order_by(table.c.id)).all()
                module = migration(sync); module.upgrade()
                after = sa.Table('kaf_files', sa.MetaData(), autoload_with=sync)
                assert after.c.source_doc_id.type.length == 64
                assert sync.execute(sa.select(after).order_by(after.c.id)).all() == before
                sync.execute(after.insert(), row('long','summary:'+'f'*36))
                with pytest.raises(RuntimeError, match='36'): module.downgrade()
                assert sa.inspect(sync).get_columns('kaf_files')[6]['type'].length == 64
                sync.execute(after.delete().where(after.c.id=='long'))
                module.downgrade()
                restored = sa.Table('kaf_files', sa.MetaData(), autoload_with=sync)
                assert restored.c.source_doc_id.type.length == 36
                assert sync.execute(sa.select(restored).order_by(restored.c.id)).all() == before
                restored.drop(sync)
            await connection.run_sync(upgrade)
    finally:
        await engine.dispose()


async def test_full_pool_skips_optional_publish_then_recovers(mysql_publisher):
    _, service, directory = mysql_publisher
    engine = create_async_engine(os.environ['KNOWALL_ROUND46_MYSQL_URL'], pool_size=1, max_overflow=0, pool_timeout=.03)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(KafFile)) == 0
            assert await publish(db, 'document', 'pool46') is None
            assert await db.scalar(select(func.count()).select_from(KafFile)) == 0
            assert not list(directory.glob('*.kaf'))
            await db.rollback()
            # Round47 keeps a dedicated MySQL source-lock connection through
            # commit. A one-slot pool must skip even after caller rollback.
            assert await publish(db, 'document', 'pool46') is None
            assert not list(directory.glob('*.kaf'))
    finally:
        await engine.dispose()
