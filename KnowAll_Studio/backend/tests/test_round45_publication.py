"""Real SQLite/RSA publication failures; never infer rollback from commit errors."""
from pathlib import Path
import pytest
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from kaf.format import unpack_kaf
from tests.test_round44_auto_publish import publisher, publish, account_app, private_kaf
from tests.test_round43_kaf_recovery import real_kaf_http
from app.models.kaf import KafFile


async def test_rejected_optional_publication_keeps_loaded_document_usable(publisher, monkeypatch):
    from app.models import Document
    from sqlalchemy import inspect
    app, service, _ = publisher
    async with app.sessions() as db:
        async with db.bind.begin() as conn: await conn.run_sync(Document.__table__.create)
        doc = Document(filename='original45.md', file_type='md', sha256='a' * 64)
        db.add(doc); await db.commit()
        saved_id = doc.id
        original = service.publish_from_markdown
        def invalid(*args, **kwargs):
            kf = original(*args, **kwargs); kf.title = None; return kf
        with monkeypatch.context() as patch:
            patch.setattr(service, 'publish_from_markdown', invalid)
            with pytest.raises(IntegrityError): await publish(db, 'document', 'optional45')
        assert not inspect(doc).expired_attributes, 'Optional publication expired the uploaded document'
        assert doc.id == saved_id and doc.filename == 'original45.md'


@pytest.mark.parametrize('kind', ['document', 'summary'])
@pytest.mark.parametrize('cleanup', ['normal', 'replacement', 'permission'])
async def test_flush_rejection_cleans_only_unchanged_new_file(publisher, monkeypatch, kind, cleanup):
    app, service, directory = publisher
    method = 'publish_from_markdown' if kind == 'document' else 'publish_from_package'
    original = getattr(service, method)
    def invalid(*args, **kwargs):
        kf = original(*args, **kwargs)
        kf.title = None  # Native NOT NULL constraint, not a fake DB exception.
        return kf
    async with app.sessions() as db:
        original_flush = db.flush
        async def rejected_flush(*args, **kwargs):
            try:
                await original_flush(*args, **kwargs)
            except IntegrityError:
                if cleanup == 'replacement':
                    next(directory.glob('*.kaf')).write_bytes(b'replacement-owned-by-someone-else')
                raise
        read = Path.read_bytes
        def denied(path):
            if cleanup == 'permission' and path.suffix == '.kaf':
                raise PermissionError('synthetic-cleanup45')
            return read(path)
        with monkeypatch.context() as patch:
            patch.setattr(service, method, invalid)
            patch.setattr(db, 'flush', rejected_flush)
            patch.setattr(Path, 'read_bytes', denied)
            with pytest.raises(IntegrityError):
                await publish(db, kind, 'rejected45')
        # Production rollback restores the actual session without caller repair.
        assert await db.scalar(select(func.count()).select_from(KafFile)) == 0
        files = list(directory.glob('*.kaf'))
        assert len(files) == (0 if cleanup == 'normal' else 1)
        if cleanup == 'replacement':
            assert files[0].read_bytes() == b'replacement-owned-by-someone-else'
        assert await publish(db, kind, 'rejected45')


@pytest.mark.parametrize('kind', ['document', 'summary'])
async def test_commit_succeeds_then_connection_error_preserves_publication(publisher, monkeypatch, kind):
    app, service, directory = publisher
    async with app.sessions() as db:
        original = db.commit
        async def uncertain():
            await original()
            raise ConnectionError('synthetic-lost-commit-response45')
        with monkeypatch.context() as patch:
            patch.setattr(db, 'commit', uncertain)
            result = await publish(db, kind, 'uncertain45')
            assert result and result['file_id']
    async with app.sessions() as db:
        rows = (await db.scalars(select(KafFile))).all()
        assert len(rows) == 1
        data = Path(rows[0].storage_path).read_bytes()
        assert unpack_kaf(data, service.private_key)['checksum_ok']
        assert await publish(db, kind, 'uncertain45') is None
        assert len(list(directory.glob('*.kaf'))) == 1


@pytest.mark.parametrize('kind', ['document', 'summary'])
def test_same_explicit_file_id_cannot_overwrite_original(publisher, kind):
    _, service, directory = publisher
    def build(text):
        if kind == 'document':
            return service.publish_from_markdown(text, 'test45', file_id='identity45')
        return service.publish_from_package({'chapters': [{'body': text}]},
                                            {'file_id': 'identity45', 'title': text})
    first = build('# original45')
    data = Path(first.storage_path).read_bytes()
    with pytest.raises(FileExistsError):
        build('# replacement45')
    assert Path(first.storage_path).read_bytes() == data
    assert unpack_kaf(data, service.private_key)['checksum_ok']


async def test_admin_native_constraint_rejection_has_no_orphan_and_retry_works(real_kaf_http, monkeypatch):
    app, client, headers, doc, _, service = real_kaf_http
    original = service.publish_from_markdown
    def invalid(*args, **kwargs):
        kf = original(*args, **kwargs); kf.title = None; return kf
    directory = Path(doc.storage_path).parent
    before = set(directory.glob('*.kaf'))
    with monkeypatch.context() as patch:
        patch.setattr(service, 'publish_from_markdown', invalid)
        response = await client.post('/api/v1/kaf/admin/publish', headers=headers,
                                     json={'markdown': '# rejected-admin45'})
        assert response.status_code == 500
    assert set(directory.glob('*.kaf')) == before
    async with app.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(KafFile)) == 1
    response = await client.post('/api/v1/kaf/admin/publish', headers=headers,
                                 json={'markdown': '# recovered-admin45'})
    assert response.status_code == 201, response.text


@pytest.mark.parametrize('kind', ['document', 'summary'])
async def test_failed_native_fsync_removes_only_new_file(publisher, monkeypatch, kind):
    from app.core import kaf_service
    app, service, directory = publisher
    old = service.publish_from_markdown('# preserved45', 'test45')
    old_bytes = Path(old.storage_path).read_bytes()
    def failed(fd): raise OSError('synthetic-disk-flush45')
    with monkeypatch.context() as patch:
        patch.setattr(kaf_service.os, 'fsync', failed)
        async with app.sessions() as db:
            assert await publish(db, kind, 'disk45') is None
            assert await db.scalar(select(func.count()).select_from(KafFile)) == 0
    assert set(directory.glob('*.kaf')) == {Path(old.storage_path)}
    assert Path(old.storage_path).read_bytes() == old_bytes
    async with app.sessions() as db: assert await publish(db, kind, 'disk45')


async def test_failed_rollback_preserves_file_and_original_constraint_error(publisher, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSessionTransaction
    app, service, directory = publisher
    original = service.publish_from_markdown
    def invalid(*args, **kwargs):
        kf = original(*args, **kwargs); kf.title = None; return kf
    async with app.sessions() as db:
        async def failed(transaction): raise ConnectionError('synthetic-rollback-outage45')
        with monkeypatch.context() as patch:
            patch.setattr(service, 'publish_from_markdown', invalid)
            patch.setattr(AsyncSessionTransaction, 'rollback', failed)
            with pytest.raises(IntegrityError): await publish(db, 'document', 'rollback45')
        assert len(list(directory.glob('*.kaf'))) == 1
        await db.rollback()
        assert await db.scalar(select(func.count()).select_from(KafFile)) == 0
        assert await publish(db, 'document', 'rollback45')
