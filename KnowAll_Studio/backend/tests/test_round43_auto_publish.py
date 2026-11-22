"""Actual publisher helper/SQLite/RSA; no provider calls or shared database."""
import asyncio
import threading
from pathlib import Path

import pytest
from sqlalchemy import event, func, select
from kaf.format import unpack_kaf
from app.core import crypto, kaf_publisher, kaf_service
from app.models.kaf import KafFile
from tests.test_product_auth import account_app
from tests.test_round41_key_recovery import private_kaf


@pytest.mark.parametrize('kind', ['document', 'summary'])
async def test_real_auto_publish_offloads_crypto_and_keeps_session_on_loop(account_app, private_kaf, tmp_path, monkeypatch, kind):
    service = kaf_service.KAFService()
    monkeypatch.setattr(kaf_publisher, 'get_kaf_service', lambda: service)
    monkeypatch.setattr(kaf_service, 'KAF_DIR', tmp_path / 'documents')
    async with account_app.sessions() as db:
        async with db.bind.begin() as conn:
            await conn.run_sync(KafFile.__table__.create)
        loop_thread = threading.get_ident()
        sql_threads = []
        def observe_sql(*args):
            sql_threads.append(threading.get_ident())
        event.listen(db.bind.sync_engine, 'before_cursor_execute', observe_sql)
        entered = threading.Event(); release = threading.Event(); crypto_threads = []
        method = 'publish_from_markdown' if kind == 'document' else 'publish_from_package'
        original = getattr(service, method)
        def observe_crypto(*args, **kwargs):
            crypto_threads.append(threading.get_ident()); entered.set(); release.wait(.8)
            return original(*args, **kwargs)
        monkeypatch.setattr(service, method, observe_crypto)
        async def publish():
            if kind == 'document':
                return await kaf_publisher.publish_document(db, 'document43', 'lesson.md',
                    markdown='# Auto43\n\nactual-auto-content43', topic='test43')
            return await kaf_publisher.publish_summary_package(db, 'summary43', 'Summary43', 'test43',
                [{'title': 'Chapter43', 'body': 'actual-auto-content43'}])
        task = asyncio.create_task(publish())
        try:
            async with asyncio.timeout(5):
                while not entered.is_set(): await asyncio.sleep(.005)
            assert crypto_threads[0] != loop_thread, 'Automatic KAF crypto blocked the event loop'
            assert not task.done()
            release.set(); result = await task
            saved = await db.scalar(select(KafFile).where(KafFile.file_id == result['file_id']))
            data = Path(saved.storage_path).read_bytes()
            decoded = unpack_kaf(data, service.private_key)
            assert decoded['checksum_ok'] and b'actual-auto-content43' in decoded['payload_bytes']
            assert saved.source_doc_id == ('document43' if kind == 'document' else 'summary:summary43')
            assert saved.node_count == (0 if kind == 'document' else 1)
            assert all(t == loop_thread for t in sql_threads)
            # Sequential idempotency must use the actual persisted row.
            assert await publish() is None
            assert len(crypto_threads) == 1
            assert await db.scalar(select(func.count()).select_from(KafFile)) == 1
            assert len(list((tmp_path / 'documents').glob('*.kaf'))) == 1
        finally:
            release.set()
            if not task.done(): await task
            event.remove(db.bind.sync_engine, 'before_cursor_execute', observe_sql)


@pytest.mark.parametrize('kind', ['document', 'summary'])
async def test_auto_publish_key_outage_stays_optional_and_recovers(account_app, private_kaf, tmp_path, monkeypatch, kind):
    first = kaf_service.KAFService(); first.public_key
    original = crypto._KEY_FILE.read_bytes(); crypto._KEY_FILE.unlink()
    fresh = kaf_service.KAFService()
    monkeypatch.setattr(kaf_publisher, 'get_kaf_service', lambda: fresh)
    monkeypatch.setattr(kaf_service, 'KAF_DIR', tmp_path / 'documents')
    async with account_app.sessions() as db:
        async with db.bind.begin() as conn:
            await conn.run_sync(KafFile.__table__.create)
        async def publish():
            if kind == 'document':
                return await kaf_publisher.publish_document(db, 'failed43', 'lesson.md', markdown='# Recovery')
            return await kaf_publisher.publish_summary_package(db, 'failed43', 'Recovery', 'test43', [])
        assert await publish() is None
        assert not crypto._KEY_FILE.exists()
        assert await db.scalar(select(func.count()).select_from(KafFile)) == 0
        crypto._KEY_FILE.write_bytes(original)
        assert (await publish())['file_id']


async def test_empty_document_skips_crypto_and_writes(account_app, private_kaf, monkeypatch):
    service = kaf_service.KAFService()
    monkeypatch.setattr(kaf_publisher, 'get_kaf_service', lambda: service)
    async with account_app.sessions() as db:
        async with db.bind.begin() as conn:
            await conn.run_sync(KafFile.__table__.create)
        assert await kaf_publisher.publish_document(db, 'empty43', 'empty.md', markdown='  ') is None
        assert service._private_key is None and not crypto._KEY_FILE.exists()
        assert await db.scalar(select(func.count()).select_from(KafFile)) == 0
