"""Query-count regression tests for MasteryAnalyzer.get_overall_stats().

Why this file exists
--------------------
The 7-day trend was built with one aggregate query per day inside a loop:

    for i in range(7):
        day = ... - timedelta(days=i)
        day_stmt = select(func.count(...), func.sum(...)).where(
            AnswerRecord.answered_at >= day,
            AnswerRecord.answered_at < next_day,
        )
        await session.execute(day_stmt)

Seven round trips for a fixed seven-row result. It is now a single GROUP BY.
The day ORDER must stay newest-first — that is what the chart consumes — so the
functional test pins the exact sequence, not just the length.
"""
import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.quiz import mastery_analyzer as ma_module
from app.core.quiz.mastery_analyzer import MasteryAnalyzer
from app.database import Base
from app.models import AnswerRecord, QuestionBank


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


async def _seed(factory, per_day: int) -> None:
    """Seed `per_day` answers today and `per_day` answers yesterday."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with factory() as session:
        for day_offset in (0, 1):
            when = now - timedelta(days=day_offset)
            for i in range(per_day):
                qid = f"q_{day_offset}_{i}"
                session.add(
                    QuestionBank(
                        id=qid,
                        question_type="single_choice",
                        question_text=f"题目{day_offset}-{i}",
                        correct_answer="A",
                        cognitive_level="L2_understand",
                    )
                )
                session.add(
                    AnswerRecord(
                        id=f"a_{day_offset}_{i}",
                        user_id="u1",
                        question_id=qid,
                        user_answer="A",
                        # today: all correct; yesterday: none correct
                        is_correct=(day_offset == 0),
                        answered_at=when,
                    )
                )
        await session.commit()


def _record_selects(engine):
    counter = {"n": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _before)

    def detach():
        event.remove(engine.sync_engine, "before_cursor_execute", _before)

    return counter, detach


async def _run_stats(engine, factory, monkeypatch, per_day: int):
    await _seed(factory, per_day)
    monkeypatch.setattr(ma_module, "async_session", factory)
    counter, detach = _record_selects(engine)
    try:
        result = await MasteryAnalyzer().get_overall_stats(user_id="u1")
    finally:
        detach()
    return result, counter["n"]


async def test_overall_stats_recent_7_days_values(sqlite_factory, monkeypatch):
    """Functional check: 7 days, newest first, with the right per-day numbers."""
    engine, factory = sqlite_factory
    result, _ = await _run_stats(engine, factory, monkeypatch, per_day=3)

    days = result["recent_7_days"]
    assert len(days) == 7

    today = datetime.now(timezone.utc).replace(tzinfo=None).date()
    assert days[0]["date"] == today.isoformat(), "first entry must be today"
    assert days[1]["date"] == (today - timedelta(days=1)).isoformat()
    assert days[6]["date"] == (today - timedelta(days=6)).isoformat(), "order must be newest-first"

    assert days[0]["total"] == 3 and days[0]["correct"] == 3
    assert days[1]["total"] == 3 and days[1]["correct"] == 0
    assert days[2]["total"] == 0 and days[2]["correct"] == 0, "empty days must still be present"

    assert result["total_answers"] == 6
    assert result["correct_answers"] == 3


async def test_overall_stats_query_count_is_flat(sqlite_factory, monkeypatch):
    """SELECT count must not scale with the number of days or records."""
    engine, factory = sqlite_factory
    _, selects_small = await _run_stats(engine, factory, monkeypatch, per_day=2)

    engine2 = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine2.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
    try:
        _, selects_large = await _run_stats(engine2, factory2, monkeypatch, per_day=50)
    finally:
        await engine2.dispose()

    # total + correct + trend GROUP BY + cognitive breakdown
    # The lower bound is load-bearing: without it this test passes even when the
    # statement counter is silently dead (0 <= budget, and 0 == 0 for the
    # growth check below). A counter that never fires makes the test unfalsifiable.
    # Found by round-21 audit — see docs/optimization-report.md §23.
    assert 0 < selects_small <= 4, (
        f"issued {selects_small} SELECTs; expected within budget "
        f"(the old per-day loop would add 7 more)"
    )
    assert 0 < selects_large <= 4, f"issued {selects_large} SELECTs; expected within budget"
    assert selects_large == selects_small, (
        f"SELECT count changed with data volume ({selects_small} -> {selects_large})"
    )
