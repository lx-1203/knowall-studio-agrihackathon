"""Distinguish version-statement budget from a whole-request guarantee."""
import time,json
from sqlalchemy import text
from tests.test_flashcard_edit_flow import edits,create,persisted

async def test_real_rollback_journal_reader_commit_is_now_retryable(edits,tmp_path):
    client,factory,_=edits;card,_=await create(client);before=await persisted(factory,card['id'])
    async with factory() as reader:
        assert await reader.scalar(text('PRAGMA journal_mode'))=='delete'
        # Python sqlite legacy SELECT alone does not begin the actual read lock.
        await reader.execute(text('BEGIN'))
        assert await reader.scalar(text('SELECT count(*) FROM flashcards'))==1
        started=time.monotonic();response=await client.get('/api/v1/flashcards/export/anki/'+card['deck_id']);elapsed=time.monotonic()-started
        await reader.rollback()
    assert response.status_code==503,response.text
    assert response.headers["retry-after"]=="1"
    assert 'private' not in response.text
    assert await persisted(factory,card['id'])==before
    assert elapsed<3.5
    result={'actual_reader_lock':True,'journal_mode':'delete','commit_not_covered_by_version_statement_budget':False,'bounded_after_round32':True,'elapsed':elapsed,'status':503,'metadata_unchanged':True,'production_wal_this_scenario_not_equivalent':True}
    (tmp_path/'commit-diagnostic.json').write_text(json.dumps(result),encoding='utf-8');print('ROUND31_COMMIT_DIAGNOSTIC '+json.dumps(result))

async def test_production_wal_reader_does_not_block_export_commit(edits):
    client,factory,_=edits;card,_=await create(client);before=await persisted(factory,card['id'])
    async with factory() as reader:
        assert await reader.scalar(text('PRAGMA journal_mode=WAL'))=='wal'
        await reader.execute(text('BEGIN'))
        assert await reader.scalar(text('SELECT count(*) FROM flashcards'))==1
        started=time.monotonic();response=await client.get('/api/v1/flashcards/export/anki/'+card['deck_id']);elapsed=time.monotonic()-started
        await reader.rollback()
    assert response.status_code==200,response.text
    assert response.content[:2]==b'PK'
    after=await persisted(factory,card['id'])
    assert before[1:]==after[1:]
    assert after[0]['anki_export_version']==after[0]['content_version']==1
    assert after[0]['anki_export_mtime'] is not None
    print('ROUND31_WAL_READER '+json.dumps({'actual_reader_lock':True,'journal_mode':'wal','status':200,'elapsed':elapsed,'schedule_unchanged':True}))
