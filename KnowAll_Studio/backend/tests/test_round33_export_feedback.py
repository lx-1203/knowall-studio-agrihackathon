"""Public retry feedback reflects real native busy or future persisted version."""
import time
from sqlalchemy import update
from app.models import Flashcard
from tests.test_flashcard_edit_flow import edits, create, persisted

async def test_real_writer_busy_does_not_claim_a_clock_problem(edits):
    client, factory, _ = edits
    card, _ = await create(client)
    before = await persisted(factory, card['id'])
    async with factory() as writer:
        await writer.execute(update(Flashcard).where(Flashcard.id==card['id']).values(hints='private-uncommitted'))
        response = await client.get('/api/v1/flashcards/export/anki/'+card['deck_id'])
        await writer.rollback()
    assert response.status_code == 503 and response.headers['retry-after'] == '1'
    assert response.json()['detail'] == '导出服务暂时繁忙，请稍后重试'
    assert await persisted(factory,card['id']) == before

async def test_actual_future_persisted_version_explains_time_sync(edits):
    client, factory, _ = edits
    card, _ = await create(client)
    async with factory() as db:
        await db.execute(update(Flashcard).where(Flashcard.id==card['id']).values(anki_export_mtime=int(time.time())+60, anki_export_version=1, anki_export_format=3))
        await db.commit()
    before = await persisted(factory,card['id'])
    response = await client.get('/api/v1/flashcards/export/anki/'+card['deck_id'])
    assert response.status_code == 503 and response.headers['retry-after'] == '1'
    assert response.json()['detail'] == '等待导出版本时间同步，请稍后重试'
    assert await persisted(factory,card['id']) == before
