"""Shared card APIs must never expose or mutate another learner's pack."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.database import Base
from app.models import Document, Deck, Flashcard, ReviewSchedule, ReviewLog, StudyPack, ReviewQueue, KnowledgeCoverage
from app.api import flashcards, memory_feedback
from app.core.memory import feedback_loop


@pytest.fixture
async def private_db(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(feedback_loop, 'async_session', factory)
    async with factory() as db:
        db.add(Document(id='doc', filename='book', file_type='md', sha256='f' * 64))
        for name in ('shared', 'own', 'private'):
            db.add(Deck(id=name, name=name, card_count=1))
            db.add(Flashcard(id=name, deck_id=name, card_type='qa', front='needle '+name,
                            back='answer', knowledge_point_id='kp-'+name, review_count=5, correct_count=0, accuracy_rate=0))
            db.add(ReviewSchedule(card_id=name, state='new', review_count=0))
            db.add_all([ReviewLog(card_id=name, rating=1) for _ in range(5)])
            db.add(KnowledgeCoverage(knowledge_point_id='kp-'+name, resource_type='flashcard', resource_id=name))
        for deck, owner in (('own', 'u1'), ('private', 'u2')):
            db.add(StudyPack(id='pack-'+deck, user_id=owner, document_id='doc', title=deck,
                             source_hash='f'*64, config={}, sections=[], deck_id=deck))
        db.add(ReviewQueue(id='private-queue', user_id='u2', resource_type='flashcard', resource_id='private'))
        await db.commit()
        yield db
    await engine.dispose()


@pytest.mark.parametrize('user,expected', [(None, {'shared'}), (SimpleNamespace(id='u1'), {'shared', 'own'})])
async def test_lists_include_legacy_shared_cards_and_only_owned_packs(private_db, user, expected):
    due = await flashcards.get_due_cards(db=private_db, current_user=user)
    assert {c['id'] for c in due['cards']} == expected
    decks = await flashcards.list_decks(db=private_db, current_user=user)
    assert {d['id'] for d in decks} == expected
    filtered = await flashcards.get_due_cards(db=private_db, current_user=user, deck_id='private')
    assert filtered['cards'] == []


async def test_private_card_routes_reject_before_export_generation_or_mutation(private_db, monkeypatch):
    user = SimpleNamespace(id='u1')
    exporter = AsyncMock()
    monkeypatch.setattr(flashcards, 'export_apkg', exporter)
    for request in (
        lambda: flashcards.review_card(flashcards.ReviewRequest(card_id='private', rating=4), db=private_db, current_user=user),
        lambda: flashcards.export_deck_anki('private', db=private_db, current_user=user),
        lambda: flashcards.generate_deck_summary('private', db=private_db, current_user=user),
        lambda: flashcards.get_related_cards('private', db=private_db, current_user=user),
        lambda: memory_feedback.record_flashcard_result('private', db=private_db, current_user=user),
        lambda: memory_feedback.get_related_flashcards('private', db=private_db, current_user=user),
        lambda: memory_feedback.complete_review_item(memory_feedback.CompleteReviewRequest(queue_id='private-queue'), db=private_db, current_user=user),
    ):
        with pytest.raises(HTTPException) as exc:
            await request()
        assert exc.value.status_code == 404
    exporter.assert_not_called()
    card = await private_db.get(Flashcard, 'private')
    assert card.review_count == 5
    assert not (await private_db.get(ReviewQueue, 'private-queue')).completed


async def test_semantic_search_filters_against_database_ownership(private_db, monkeypatch):
    from app.core.memory.retrieval import card_retriever
    monkeypatch.setattr(card_retriever, 'search', AsyncMock(return_value=[
        {'id':'private', 'front':'secret'}, {'id':'own'}, {'id':'shared'}, {'id':'missing'}]))
    result = await flashcards.search_cards('needle', db=private_db, current_user=SimpleNamespace(id='u1'))
    assert {c['id'] for c in result['results']} == {'own', 'shared'}


async def test_feedback_stats_scan_and_decay_exclude_private_cards(private_db):
    stats = await feedback_loop.feedback_engine.get_memory_stats('u1')
    assert stats['total_cards'] == 2
    assert stats['total_reviews'] == 10
    result = await feedback_loop.feedback_engine.scan('u1')
    assert result['total_scanned'] == 2
    queues = (await private_db.execute(select(ReviewQueue).where(ReviewQueue.user_id == 'u1'))).scalars().all()
    assert {q.resource_id for q in queues} == {'shared', 'own'}
    decaying = await feedback_loop.feedback_engine.detect_decay('u1')
    assert {c['card_id'] for c in decaying} == {'shared', 'own'}


async def test_owner_and_legacy_shared_cards_remain_reviewable(private_db):
    for card_id in ('own', 'shared'):
        result = await flashcards.review_card(flashcards.ReviewRequest(card_id=card_id, rating=4),
                                              db=private_db, current_user=SimpleNamespace(id='u1'))
        assert result['review_count'] == 1


async def test_keyword_fallback_filters_private_results(private_db, monkeypatch):
    from app.core.memory.retrieval import card_retriever
    monkeypatch.setattr(card_retriever, 'search', AsyncMock(side_effect=RuntimeError('offline')))
    result = await flashcards.search_cards('needle', db=private_db, current_user=SimpleNamespace(id='u1'))
    assert result['search_method'] == 'keyword_fallback'
    assert {c['id'] for c in result['results']} == {'own', 'shared'}


async def test_related_candidates_do_not_cross_private_decks(private_db):
    for card_id in ('own', 'private'):
        (await private_db.get(Flashcard, card_id)).knowledge_point_id = 'kp-shared'
    await private_db.commit()
    result = await memory_feedback.get_related_flashcards('shared', db=private_db, current_user=SimpleNamespace(id='u1'))
    assert {c['id'] for c in result['cards']} == {'own'}
    anonymous = await flashcards.get_related_cards('shared', db=private_db, current_user=None)
    assert anonymous['cards'] == []
