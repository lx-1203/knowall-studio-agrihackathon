"""Diagnostic boundaries: actual temporary SQLAlchemy/SQLite, no paid model."""
import asyncio,json,time
from pathlib import Path
from sqlalchemy import update
from app.models import Flashcard
from tests.test_flashcard_edit_flow import edits,create,persisted
from tests.test_anki_version_flow import boundary

async def test_real_sqlite_writer_returns_bounded_safe_retry_after_round31(edits, tmp_path):
    client,factory,_=edits
    card,_=await create(client)
    before=await persisted(factory,card['id'])
    async with factory() as writer:
        await writer.execute(update(Flashcard).where(Flashcard.id==card['id']).values(hints='uncommitted-private-lock'))
        start=time.monotonic()
        response=await client.get('/api/v1/flashcards/export/anki/'+card['deck_id'])
        elapsed=time.monotonic()-start
        assert response.status_code==503,response.text
        assert response.headers['retry-after']=='1'
        assert 'uncommitted-private-lock' not in response.text
        await writer.rollback()
    assert await persisted(factory,card['id'])==before
    # The original round30 log preserves the 6.38s failure. Current code caps
    # export SQL busy waits without changing the general connection timeout.
    assert elapsed<3
    evidence={'actual_sqlite_write_lock':True,'elapsed':elapsed,'status':503,'retry_after':response.headers['retry-after'],'metadata_unchanged':True,'driver_wait_exceeded_application_loop_budget':False,'bounded_after_round31':True}
    (tmp_path/'lock-evidence.json').write_text(json.dumps(evidence),encoding='utf-8')
    print('ROUND30_LOCK_DIAGNOSTIC '+json.dumps(evidence))

async def test_mixed_clock_fixture_can_exhaust_real_budget_without_wall_advancing(edits,boundary,monkeypatch,tmp_path):
    client,_,_=edits;card,_=await create(client)
    from app.core import anki_versions
    ticks=iter([100,100,101.9])
    monkeypatch.setattr(anki_versions._clock,'monotonic',lambda:next(ticks,102.1))
    response=await client.get('/api/v1/flashcards/export/anki/'+card['deck_id'])
    assert response.status_code==503 and response.headers['retry-after']=='1'
    assert boundary.now==1790000000.25 and not boundary.sleeps
    print('ROUND30_MIXED_CLOCK_DIAGNOSTIC '+json.dumps({'injected_monotonic_elapsed':1.9,'wall_advanced':0,'clock_wait_needed':.75,'remaining_budget':.1,'status':503,'historical_root_cause_directly_observed':False}))

