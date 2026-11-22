"""Actual SQLite writer contention and pooled timeout restoration."""
import asyncio,json,time
import pytest
from sqlalchemy import update,text,event
from app.models import Flashcard
from tests.test_flashcard_edit_flow import edits,create,persisted

async def timeout_values(factory):
    # Hold two connections concurrently: inspect all connections this fixture used.
    async with factory() as one, factory() as two:
        return [await one.scalar(text('PRAGMA busy_timeout')),await two.scalar(text('PRAGMA busy_timeout'))]

async def test_continuous_writer_respects_export_budget_and_restores_pool(edits,tmp_path):
    client,factory,_=edits; card,_=await create(client); before=await persisted(factory,card['id'])
    assert await timeout_values(factory)==[5000,5000]
    async with factory() as writer:
        await writer.execute(update(Flashcard).where(Flashcard.id==card['id']).values(hints='private-uncommitted'))
        started=time.monotonic();response=await client.get('/api/v1/flashcards/export/anki/'+card['deck_id']);elapsed=time.monotonic()-started
        await writer.rollback()
    assert response.status_code==503,response.text
    assert response.headers['retry-after']=='1' and 'private-uncommitted' not in response.text
    assert await persisted(factory,card['id'])==before
    assert await timeout_values(factory)==[5000,5000]
    print('ROUND31_PERSISTENT_LOCK '+json.dumps({'elapsed':elapsed,'status':response.status_code,'timeout_restored':True,'metadata_unchanged':True}))
    assert elapsed<3, f'native driver exceeded export budget: {elapsed:.3f}s'

async def test_short_writer_releases_and_real_export_succeeds(edits,tmp_path):
    client,factory,_=edits;card,_=await create(client)
    async with factory() as writer:
        await writer.execute(update(Flashcard).where(Flashcard.id==card['id']).values(hints='private-uncommitted'))
        task=asyncio.create_task(client.get('/api/v1/flashcards/export/anki/'+card['deck_id']))
        # Real bounded contention is the product scenario, not idle time padding.
        await asyncio.sleep(.35);await writer.rollback();response=await task
    assert response.status_code==200,response.text
    assert response.content[:2]==b'PK'
    assert await timeout_values(factory)==[5000,5000]

async def test_cancel_during_writer_wait_restores_timeout_and_metadata(edits):
    client,factory,_=edits;card,_=await create(client);before=await persisted(factory,card['id'])
    async with factory() as writer:
        await writer.execute(update(Flashcard).where(Flashcard.id==card['id']).values(hints='private-uncommitted'))
        entered=asyncio.Event();engine=factory.kw['bind']
        def executing(connection,cursor,statement,parameters,context,many):
            if statement.lstrip().startswith('UPDATE flashcards'): entered.set()
        event.listen(engine.sync_engine,'before_cursor_execute',executing)
        task=asyncio.create_task(client.get('/api/v1/flashcards/export/anki/'+card['deck_id']))
        await asyncio.wait_for(entered.wait(),3)
        await asyncio.sleep(.01);started=time.monotonic();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        elapsed=time.monotonic()-started;await writer.rollback()
    assert await persisted(factory,card['id'])==before
    assert await timeout_values(factory)==[5000,5000]
    print('ROUND31_CANCEL '+json.dumps({'client_task_cancel_seconds':elapsed,'timeout_restored':True}))
    assert elapsed<.6, f'driver continues waiting after cancellation: {elapsed:.3f}s'

async def test_native_nonbusy_error_keeps_original_custom_timeout(edits):
    from app.core.anki_versions import _execute_with_budget
    from sqlalchemy.exc import OperationalError
    _,factory,_=edits
    async with factory() as db:
        connection=await db.connection()
        await connection.exec_driver_sql('PRAGMA busy_timeout=731')
        with pytest.raises(OperationalError,match='no such table'):
            await _execute_with_budget(db,text('SELECT * FROM nonexistent_round31'),time.monotonic()+2)
        assert (await connection.exec_driver_sql('PRAGMA busy_timeout')).scalar_one()==731
        assert await db.scalar(text('SELECT 1'))==1
        await connection.exec_driver_sql('PRAGMA busy_timeout=5000')

async def test_zero_native_timeout_is_preserved(edits):
    from app.core.anki_versions import _execute_with_budget
    _,factory,_=edits
    async with factory() as db:
        connection=await db.connection();await connection.exec_driver_sql('PRAGMA busy_timeout=0')
        result=await _execute_with_budget(db,text('SELECT 1'),time.monotonic()+2)
        assert result.scalar_one()==1
        assert (await connection.exec_driver_sql('PRAGMA busy_timeout')).scalar_one()==0
        await connection.exec_driver_sql('PRAGMA busy_timeout=5000')
