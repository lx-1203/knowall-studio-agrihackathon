from datetime import datetime, timezone
import pytest
from app.models import Deck
from tests.test_manual_deck_flow import decks, create

async def test_new_deck_timestamp_has_explicit_utc_and_same_persisted_instant(decks):
    client, factory, _ = decks
    result = await create(client)
    instant = datetime.fromisoformat(result['created_at'])
    assert instant.tzinfo is not None, result
    assert instant.utcoffset().total_seconds() == 0
    async with factory() as db:
        stored = await db.get(Deck, result['id'])
        assert instant.replace(tzinfo=None) == stored.created_at
    listed = (await client.get('/api/v1/flashcards/decks')).json()
    assert listed[0]['created_at'] == result['created_at']

@pytest.mark.parametrize('instant',[datetime(2026,10,9,19,40,0,123456),datetime(2026,12,31,23,59,59),datetime(2026,3,8,7,0)])
async def test_legacy_database_time_unchanged_but_list_is_explicit_utc(decks,instant):
    client, factory, actor = decks
    async with factory() as db:
        db.add(Deck(id='old',name='旧UTC牌组',created_at=instant,user_id='A'))
        await db.commit()
    row=(await client.get('/api/v1/flashcards/decks')).json()[0]
    parsed=datetime.fromisoformat(row['created_at'])
    assert parsed.tzinfo is not None
    assert parsed == instant.replace(tzinfo=timezone.utc)
    async with factory() as db:
        assert (await db.get(Deck,'old')).created_at == instant
    actor.id='B'
    assert (await client.get('/api/v1/flashcards/decks')).json() == []

@pytest.mark.parametrize('offset',[8,-4,0])
def test_aware_database_adapter_preserves_instant(offset):
    from datetime import timedelta
    from app.api.flashcards import _deck_created_at
    instant=datetime(2026,10,10,3,40,tzinfo=timezone(timedelta(hours=offset)))
    parsed=datetime.fromisoformat(_deck_created_at(instant))
    assert parsed == instant
    assert parsed.utcoffset().total_seconds() == 0
