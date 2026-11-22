"""Native begin_nested preflush failures before registering the new KAF object."""
from pathlib import Path
import pytest
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from tests.test_round44_auto_publish import publisher, publish, account_app, private_kaf
from app.core import kaf_service
from app.models.kaf import KafFile


def invalid_pending():
    return KafFile(file_id='unrelated-invalid47',title=None,topic='private47',file_size=0,storage_path='',sha256='a'*64)


@pytest.mark.parametrize('kind', ['document','summary'])
@pytest.mark.parametrize('cleanup', ['normal','replacement','permission'])
async def test_native_pre_savepoint_failure_cleans_only_new_unchanged_file(publisher, monkeypatch, kind, cleanup):
    app, service, directory=publisher
    async with app.sessions() as db:
        register=kaf_service.register_publication
        async def caller_pending(session, kf):
            session.add(invalid_pending())
            return await register(session,kf)
        original=db.begin_nested
        async def begin():
            try: return await original()
            except IntegrityError:
                if cleanup=='replacement': next(directory.glob('*.kaf')).write_bytes(b'other-owner47')
                raise
        read=Path.read_bytes
        def denied(path):
            if cleanup=='permission' and path.suffix=='.kaf': raise PermissionError('private-cleanup47')
            return read(path)
        with monkeypatch.context() as patch:
            patch.setattr(db,'begin_nested',begin)
            patch.setattr(kaf_service,'register_publication',caller_pending)
            patch.setattr(Path,'read_bytes',denied)
            with pytest.raises(IntegrityError): await publish(db,kind,'preflush47')
        # Native caller transaction failed; the optional helper must not reset it.
        assert not db.is_active
        files=list(directory.glob('*.kaf'))
        assert len(files)==(0 if cleanup=='normal' else 1)
        if cleanup=='replacement': assert files[0].read_bytes()==b'other-owner47'
        await db.rollback()
        assert await db.scalar(select(func.count()).select_from(KafFile))==0
        assert await publish(db,kind,'preflush47')


async def test_pre_savepoint_failure_keeps_already_registered_file(publisher):
    app, service, directory=publisher
    async with app.sessions() as db:
        assert await publish(db,'document','persisted47')
        row=(await db.scalars(select(KafFile))).one()
        path=Path(row.storage_path); data=path.read_bytes()
        db.add(invalid_pending())
        with pytest.raises(IntegrityError): await kaf_service.register_publication(db,row)
        assert path.read_bytes()==data
        await db.rollback()
