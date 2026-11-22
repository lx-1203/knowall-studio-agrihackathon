"""Use disposable real SQLAlchemy databases for every scheduling entry."""
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import Base
from app.models import Flashcard, ReviewSchedule, ReviewLog, RecitationPassage, RecitationRecord
from app.api import flashcards, recitation, backup
import app.core.memory as memory
import app.models
from tests.test_fsrs_library_integration import Clock


@pytest.fixture
async def db():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def clock(monkeypatch):
    Clock.current = datetime(2026, 10, 9, tzinfo=timezone.utc)
    monkeypatch.setattr(memory, 'datetime', Clock)
    return Clock


async def test_legacy_flashcard_persists_version_and_reuses_standard_state(db, clock):
    db.add(Flashcard(id='legacy', card_type='basic', front='Q', back='A'))
    db.add(ReviewSchedule(card_id='legacy', state='review', fsrs_stability=5,
                          fsrs_difficulty=0.5, review_count=7,
                          last_review_at=clock.current.replace(tzinfo=None) - timedelta(days=5)))
    await db.commit()
    response = await flashcards.review_card(flashcards.ReviewRequest(card_id='legacy', rating=3), db=db, current_user=None)
    db.expire_all()
    schedule = (await db.execute(select(ReviewSchedule))).scalar_one()
    assert schedule.fsrs_version == 'fsrs-6'
    assert response['review_count'] == schedule.review_count == 8
    clock.current = schedule.next_review_at.replace(tzinfo=timezone.utc)
    await flashcards.review_card(flashcards.ReviewRequest(card_id='legacy', rating=3), db=db, current_user=None)
    db.expire_all()
    schedule = (await db.execute(select(ReviewSchedule))).scalar_one()
    assert schedule.fsrs_version == 'fsrs-6' and schedule.review_count == 9
    assert schedule.next_review_at - schedule.last_review_at > timedelta(days=1)
    assert len((await db.execute(select(ReviewLog))).scalars().all()) == 2


async def test_recitation_uses_last_review_instead_of_creation_time(db, clock):
    db.add(RecitationPassage(id='passage', title='test', content='text', user_id='u'))
    db.add(RecitationRecord(id='recitation', passage_id='passage', mode='dictation', user_id='u',
                            state='new', created_at=datetime(2020, 1, 1)))
    await db.commit()
    user = SimpleNamespace(id='u')
    await recitation.review_passage(recitation.ReviewRequest(record_id='recitation', rating=4), db=db, current_user=user)
    db.expire_all()
    record = await db.get(RecitationRecord, 'recitation')
    assert record.last_review_at == clock.current.replace(tzinfo=None)
    assert record.fsrs_version == 'fsrs-6'
    expected_input = dict(stability=record.stability, difficulty=record.difficulty_score,
                          state=record.state, next_review_at=record.next_review_at,
                          last_review_at=record.last_review_at, review_count=1, fsrs_version=record.fsrs_version)
    clock.current = record.next_review_at.replace(tzinfo=timezone.utc)
    expected = memory.fsrs.review(expected_input, 3)
    response = await recitation.review_passage(recitation.ReviewRequest(record_id='recitation', rating=3), db=db, current_user=user)
    assert response['stability'] == pytest.approx(expected['stability'])
    assert record.last_review_at == clock.current.replace(tzinfo=None)
    assert record.created_at == datetime(2020, 1, 1) and record.review_count == 2


async def test_backup_roundtrip_preserves_library_marker_and_last_review(db, tmp_path, monkeypatch):
    monkeypatch.setattr(backup.settings, 'export_dir', str(tmp_path))
    original = dict(state='review', stability=5, difficulty=1, retrievability=0.9,
                    next_review_at='2026-10-14T00:00:00', last_review_at='2026-10-09T00:00:00',
                    review_count=3, fsrs_version='fsrs-6')
    data = dict(version='0.2', data={'flashcards': [dict(id='restored', card_type='basic', front='Q', back='A', schedule=original)]})
    file = UploadFile(io.BytesIO(json.dumps(data).encode()), filename='backup.json')
    await backup.import_data(file, db=db, current_user=SimpleNamespace(id='u'))
    schedule = (await db.execute(select(ReviewSchedule))).scalar_one()
    assert schedule.fsrs_version == 'fsrs-6'
    assert schedule.last_review_at == datetime(2026, 10, 9)
    response = await backup.export_all_data(db=db, current_user=SimpleNamespace(id='u'))
    exported = json.loads(Path(response.path).read_text(encoding='utf-8'))
    assert exported['data']['flashcards'][0]['schedule'] == original


async def test_legacy_backup_without_marker_remains_readable(db):
    data = dict(version='0.1', data={'flashcards': [dict(id='old', card_type='basic', front='Q', back='A',
                  schedule=dict(state='review', stability=5, difficulty=0.5, review_count=3))]})
    await backup.import_data(UploadFile(io.BytesIO(json.dumps(data).encode()), filename='old.json'), db=db, current_user=SimpleNamespace(id='u'))
    schedule = (await db.execute(select(ReviewSchedule))).scalar_one()
    assert schedule.fsrs_version is None and schedule.review_count == 3


@pytest.mark.parametrize('last_date,expected', [('2026-10-09T08:00:00+08:00', datetime(2026, 10, 9)), ('bad-date', None)])
async def test_backup_dates_normalize_to_utc_or_use_legacy_missing_date(db, last_date, expected):
    data = dict(version='0.2', data={'flashcards': [dict(id='date', card_type='basic', front='Q', back='A',
                  schedule=dict(state='review', stability=5, difficulty=0.5, review_count=3, last_review_at=last_date))]})
    await backup.import_data(UploadFile(io.BytesIO(json.dumps(data).encode()), filename='old.json'), db=db, current_user=SimpleNamespace(id='u'))
    schedule = (await db.execute(select(ReviewSchedule))).scalar_one()
    assert schedule.last_review_at == expected


async def test_learning_pack_review_persists_the_same_library_state(db, clock):
    from tests.test_learning_api import make_pack
    from app.models import Document, DocumentChunk
    from app.core.study_pack_service import review_pack_card
    db.add(Document(id='doc', filename='test.md', file_type='md', sha256='a' * 64, status='ready'))
    db.add(DocumentChunk(id='chunk', doc_id='doc', chunk_index=0, text_content='# Section\nCore concept'))
    await db.commit()
    pack = await make_pack(db)
    card_id = pack['sections'][0]['flashcards'][0]['id']
    await review_pack_card(db, pack['id'], 'u1', card_id, 4)
    db.expire_all()
    schedule = (await db.execute(select(ReviewSchedule).where(ReviewSchedule.card_id == card_id))).scalar_one()
    assert schedule.fsrs_version == 'fsrs-6' and schedule.review_count == 1
    clock.current = schedule.next_review_at.replace(tzinfo=timezone.utc)
    await review_pack_card(db, pack['id'], 'u1', card_id, 3)
    assert schedule.review_count == 2 and schedule.fsrs_stability > 0
