"""Real commit contention, native SQLite rollback and temporary file ownership."""
import asyncio,time,json
from pathlib import Path
from sqlalchemy import text
from tests.test_flashcard_edit_flow import edits,create,persisted
from tests.test_round31_export_budget import timeout_values

async def test_reader_blocked_commit_returns_bounded_503_and_recovers(edits,tmp_path,monkeypatch):
    from app.api import flashcards
    client,factory,_=edits;card,_=await create(client);before=await persisted(factory,card['id']);paths=[];original=flashcards.tempfile.mkstemp
    def capture(*args,**kwargs):
        descriptor,path=original(*args,**kwargs);paths.append(Path(path));return descriptor,path
    monkeypatch.setattr(flashcards.tempfile,'mkstemp',capture)
    async with factory() as reader:
        assert await reader.scalar(text('PRAGMA journal_mode'))=='delete'
        await reader.execute(text('BEGIN'));assert await reader.scalar(text('SELECT count(*) FROM flashcards'))==1
        started=time.monotonic();response=await client.get('/api/v1/flashcards/export/anki/'+card['deck_id']);elapsed=time.monotonic()-started
        await reader.rollback()
    assert await persisted(factory,card['id'])==before
    assert paths and all(not p.exists() for p in paths)
    assert await timeout_values(factory)==[5000,5000]
    print('ROUND32_COMMIT_LOCK '+json.dumps({'elapsed':elapsed,'status':response.status_code,'metadata_unchanged':True,'owned_files_removed':True,'pool_timeout_restored':True}))
    assert response.status_code==503,response.text
    assert response.headers['retry-after']=='1'
    assert elapsed<3.5
    recovered=await client.get('/api/v1/flashcards/export/anki/'+card['deck_id'])
    assert recovered.status_code==200 and recovered.content[:2]==b'PK'
    assert all(not p.exists() for p in paths)
    assert (await persisted(factory,card['id']))[0]['anki_export_version']==1

async def test_successful_exports_do_not_change_pool_timeout(edits):
    client,factory,_=edits;card,_=await create(client)
    assert (await client.get('/api/v1/flashcards/export/anki/'+card['deck_id'])).status_code==200
    assert await timeout_values(factory)==[5000,5000]
    assert (await client.get('/api/v1/flashcards/export/anki/'+card['deck_id'])).status_code==200
    assert await timeout_values(factory)==[5000,5000]

async def test_export_with_single_connection_pool_does_not_deadlock(edits):
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from app.database import get_db
    client,factory,_=edits;card,_=await create(client)
    engine=create_async_engine(factory.kw['bind'].url,pool_size=1,max_overflow=0,pool_timeout=.4)
    bounded=async_sessionmaker(engine,expire_on_commit=False)
    async def database():
        async with bounded() as db:yield db
    app=client._transport.app;original=app.dependency_overrides[get_db];app.dependency_overrides[get_db]=database
    try:
        response=await asyncio.wait_for(client.get('/api/v1/flashcards/export/anki/'+card['deck_id']),5)
        assert response.status_code==200,response.text
        assert engine.pool.checkedout()==0
        async with bounded() as db:
            assert await db.scalar(text('PRAGMA busy_timeout'))==5000
    finally:
        app.dependency_overrides[get_db]=original;await engine.dispose()

async def test_cancel_during_native_commit_rolls_back_and_removes_file(edits,monkeypatch):
    from sqlalchemy import event
    from app.api import flashcards
    client,factory,_=edits;card,_=await create(client);before=await persisted(factory,card['id']);paths=[];original=flashcards.tempfile.mkstemp
    def capture(*args,**kwargs):
        descriptor,path=original(*args,**kwargs);paths.append(Path(path));return descriptor,path
    monkeypatch.setattr(flashcards.tempfile,'mkstemp',capture)
    entered=asyncio.Event();engine=factory.kw['bind']
    event.listen(engine.sync_engine,'commit',lambda connection:entered.set())
    async with factory() as reader:
        await reader.execute(text('BEGIN'));await reader.scalar(text('SELECT count(*) FROM flashcards'))
        task=asyncio.create_task(client.get('/api/v1/flashcards/export/anki/'+card['deck_id']))
        await asyncio.wait_for(entered.wait(),4);await asyncio.sleep(.01);started=time.monotonic();task.cancel()
        import pytest
        with pytest.raises(asyncio.CancelledError):await task
        elapsed=time.monotonic()-started;await reader.rollback()
    assert paths and all(not p.exists() for p in paths)
    assert await persisted(factory,card['id'])==before
    assert await timeout_values(factory)==[5000,5000]
    print('ROUND32_NATIVE_COMMIT_CANCEL '+json.dumps({'elapsed':elapsed,'metadata_unchanged':True,'files_removed':True,'pool_clean':True}))
    assert elapsed<1


import pytest

@pytest.mark.parametrize("configured", [0, 731])
async def test_native_commit_preserves_shorter_custom_pool_timeout(edits, configured):
    from app.core.anki_versions import anki_export_session, commit_anki_export
    _, factory, _ = edits
    async with factory() as request_db:
        async with anki_export_session(request_db) as export_db:
            connection = await export_db.connection()
            await connection.exec_driver_sql(f"PRAGMA busy_timeout={configured}")
            await commit_anki_export(export_db)
            assert (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar_one() == configured
    async with factory() as fresh:
        assert await fresh.scalar(text("PRAGMA busy_timeout")) == configured

async def test_native_commit_can_recover_when_reader_releases_within_budget(edits):
    from sqlalchemy import event
    client, factory, _ = edits
    card, _ = await create(client)
    entered = asyncio.Event()
    callback = lambda connection: entered.set()
    engine = factory.kw["bind"]
    event.listen(engine.sync_engine, "commit", callback)
    try:
        async with factory() as reader:
            await reader.execute(text("BEGIN"))
            await reader.scalar(text("SELECT count(*) FROM flashcards"))
            task = asyncio.create_task(client.get("/api/v1/flashcards/export/anki/" + card["deck_id"]))
            try:
                await asyncio.wait_for(entered.wait(), 4)
                await reader.rollback()
                response = await asyncio.wait_for(task, 3)
            finally:
                if not task.done():
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
        assert response.status_code == 200, response.text
        assert response.content[:2] == b"PK"
        assert (await persisted(factory, card["id"]))[0]["anki_export_version"] == 1
        assert await timeout_values(factory) == [5000, 5000]
    finally:
        event.remove(engine.sync_engine, "commit", callback)


async def test_pool_exhaustion_boundary_and_recovery_are_explicit(edits):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.database import get_db
    client, factory, _ = edits
    card, _ = await create(client)
    before = await persisted(factory, card["id"])
    engine = create_async_engine(factory.kw["bind"].url, pool_size=1, max_overflow=0, pool_timeout=.2)
    bounded = async_sessionmaker(engine, expire_on_commit=False)
    async def database():
        async with bounded() as db:
            yield db
    app = client._transport.app
    original = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = database
    try:
        async with engine.connect():
            started = time.monotonic()
            response = await client.get("/api/v1/flashcards/export/anki/" + card["deck_id"])
            assert response.status_code == 503 and response.headers["retry-after"] == "1"
            elapsed = time.monotonic() - started
            assert .15 <= elapsed < 1
        assert await persisted(factory, card["id"]) == before
        response = await client.get("/api/v1/flashcards/export/anki/" + card["deck_id"])
        assert response.status_code == 200, response.text
        assert engine.pool.checkedout() == 0
        print("ROUND32_POOL_BOUNDARY " + json.dumps({"elapsed": elapsed, "native_timeout_503_after_round33": True, "release_recovers": True, "production_pool_timeout_not_changed": True}))
    finally:
        app.dependency_overrides[get_db] = original
        await engine.dispose()
