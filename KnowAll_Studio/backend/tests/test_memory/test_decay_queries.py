"""Query-count regression tests for FeedbackEngine.detect_decay().

Why this file exists
--------------------
For every card with enough review history the method issued a query for its
recent ReviewLogs, and then — for each card that looked like it was decaying —
*another* query to check whether it was already queued. That is up to 2 queries
per card, on the `/memory-feedback/decay` endpoint.

Both lookups are now batched. The review-log query selects only
(card_id, rating) and is ordered in SQL, so the per-card top-N slice is exact.

Note the deliberate trade-off: batching the logs fetches every log row for the
candidate cards (two columns) instead of `lookback_reviews` rows each. Round
trips dominate here, but the row count is reported by this test so the trade-off
stays visible. Bounding it properly needs ROW_NUMBER(), i.e. MySQL 8+ — the
project uses no window functions today, so that is a separate decision.
"""
from collections import defaultdict

import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.memory import feedback_loop as fl_module
from app.core.memory.feedback_loop import FeedbackEngine
from app.database import Base
from app.models import Flashcard, ReviewLog

LOOKBACK = 5
THRESHOLD = 2
# rating 1 == AGAIN; three of these make low_count 3 >= threshold 2 -> decaying
RATINGS = [1, 1, 1, 3, 4]


@pytest.fixture
async def sqlite_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield engine, factory
    finally:
        await engine.dispose()


async def _seed(factory, n_cards: int) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with factory() as session:
        for i in range(n_cards):
            cid = f"card_{i}"
            session.add(
                Flashcard(
                    id=cid,
                    card_type="qa",
                    front=f"正面{i}",
                    back=f"背面{i}",
                    knowledge_point_id=f"kp_{i}",
                    review_count=len(RATINGS),
                )
            )
            for j, rating in enumerate(RATINGS):
                session.add(
                    ReviewLog(
                        id=f"log_{i}_{j}",
                        card_id=cid,
                        rating=rating,
                        review_at=now - timedelta(days=j),
                    )
                )
        await session.commit()


def _record_selects(engine):
    counter = {"n": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1
        # track how many rows the batched log query returns
        if "review_log" in statement.lower() and statement.lstrip().upper().startswith("SELECT"):
            counter["log_query"] = True

    event.listen(engine.sync_engine, "before_cursor_execute", _before)

    def detach():
        event.remove(engine.sync_engine, "before_cursor_execute", _before)

    return counter, detach


async def _detect(engine, factory, monkeypatch, n_cards: int):
    await _seed(factory, n_cards)
    monkeypatch.setattr(fl_module, "async_session", factory)
    counter, detach = _record_selects(engine)
    try:
        result = await FeedbackEngine().detect_decay(
            user_id="u1", lookback_reviews=LOOKBACK, decay_threshold=THRESHOLD
        )
    finally:
        detach()
    return result, counter["n"]


async def test_detect_decay_flags_low_rated_cards(sqlite_factory, monkeypatch):
    """Functional check: decaying cards are found and reported correctly."""
    engine, factory = sqlite_factory
    result, _ = await _detect(engine, factory, monkeypatch, n_cards=3)

    assert len(result) == 3
    first = result[0]
    assert first["card_id"] == "card_0"
    assert first["front"] == "正面0"
    assert first["knowledge_point_id"] == "kp_0"
    # most recent first, exactly lookback_reviews of them
    assert first["recent_ratings"] == RATINGS
    assert first["low_count"] == 3


async def test_detect_decay_ignores_healthy_cards(sqlite_factory, monkeypatch):
    """A card whose recent ratings are all GOOD/EASY must not be flagged."""
    engine, factory = sqlite_factory
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with factory() as session:
        session.add(
            Flashcard(id="good", card_type="qa", front="f", back="b", review_count=5)
        )
        for j, rating in enumerate([4, 4, 4, 3, 3]):
            session.add(
                ReviewLog(id=f"g{j}", card_id="good", rating=rating, review_at=now - timedelta(days=j))
            )
        await session.commit()

    monkeypatch.setattr(fl_module, "async_session", factory)
    result = await FeedbackEngine().detect_decay(
        user_id="u1", lookback_reviews=LOOKBACK, decay_threshold=THRESHOLD
    )
    assert result == []


async def test_detect_decay_query_count_is_flat(sqlite_factory, monkeypatch):
    """SELECT count must not scale with the number of candidate cards."""
    engine, factory = sqlite_factory
    _, selects_small = await _detect(engine, factory, monkeypatch, n_cards=2)

    engine2 = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine2.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
    try:
        _, selects_large = await _detect(engine2, factory2, monkeypatch, n_cards=40)
    finally:
        await engine2.dispose()

    # The lower bound is load-bearing: without it this test passes even when the
    # statement counter is silently dead (0 <= budget, and 0 == 0 for the
    # growth check below). A counter that never fires makes the test unfalsifiable.
    # Found by round-21 audit — see docs/optimization-report.md §23.
    assert 0 < selects_small <= 3, (
        f"2 cards issued {selects_small} SELECTs; expected within budget (cards + batched logs + batched queue check)"
    )
    assert 0 < selects_large <= 3, (
        f"40 cards issued {selects_large} SELECTs; expected within budget "
        f"(the old per-card lookups would issue 1 + 40 + 40 = 81)"
    )
    assert selects_large == selects_small, (
        f"SELECT count grew with card count ({selects_small} -> {selects_large}): "
        f"a per-card lookup is back inside the loop"
    )
