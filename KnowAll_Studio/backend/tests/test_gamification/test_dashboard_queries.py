"""Query-count regression tests for GET /gamification/dashboard.

Why this file exists
--------------------
The dashboard assembled its weekly chart with one query per day, and fetched
today's row separately first — so today was read twice and the endpoint spent
eight round trips on streak rows that one windowed SELECT returns:

    today_streak  = await db.execute(select(LearningStreak).where(... == today))
    for i in range(6, -1, -1):
        day_result = await db.execute(select(LearningStreak).where(... == d))

It is now a single query over the 7-day window.

Note the ORDER: `week_data` is oldest-first (`range(6, -1, -1)`), which is the
opposite of MasteryAnalyzer's newest-first trend. The functional test pins the
exact sequence so a refactor cannot silently flip the chart.
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.gamification import gamification_dashboard
from app.database import Base
from app.models import LearningStreak, User


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


async def _seed(factory, days_filled: int) -> User:
    """Seed a user with streak rows on the most recent `days_filled` days."""
    today = date.today()
    async with factory() as session:
        user = User(id="u1", username="u1", email="u1@example.com", password_hash="x")
        session.add(user)
        for i in range(days_filled):
            d = today - timedelta(days=i)
            session.add(
                LearningStreak(
                    id=f"ls_{i}",
                    user_id="u1",
                    streak_date=d,
                    study_minutes=10 * (i + 1),
                    questions_answered=i + 1,
                    cards_reviewed=i + 2,
                )
            )
        await session.commit()
        return user


def _record_selects(engine):
    counter = {"n": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _before)

    def detach():
        event.remove(engine.sync_engine, "before_cursor_execute", _before)

    return counter, detach


async def _run_dashboard(engine, factory, days_filled: int):
    user = await _seed(factory, days_filled)
    counter, detach = _record_selects(engine)
    try:
        async with factory() as session:
            result = await gamification_dashboard(current_user=user, db=session)
    finally:
        detach()
    return result, counter["n"]


async def test_dashboard_week_data_values_and_order(sqlite_factory):
    """Functional check: 7 entries, oldest-first, with the right per-day numbers."""
    engine, factory = sqlite_factory
    result, _ = await _run_dashboard(engine, factory, days_filled=7)

    week = result["week_data"]
    today = date.today()

    assert len(week) == 7
    assert week[0]["date"] == (today - timedelta(days=6)).isoformat(), "must start 6 days ago"
    assert week[6]["date"] == today.isoformat(), "must end today (oldest-first)"

    # i=0 seeded today with minutes=10, questions=1, cards=2
    assert week[6]["minutes"] == 10 and week[6]["questions"] == 1 and week[6]["cards"] == 2
    # i=6 seeded 6 days ago with minutes=70
    assert week[0]["minutes"] == 70

    assert result["today_minutes"] == 10


async def test_dashboard_fills_missing_days_with_zero(sqlite_factory):
    """Days with no row must still appear, zero-filled."""
    engine, factory = sqlite_factory
    result, _ = await _run_dashboard(engine, factory, days_filled=2)

    week = result["week_data"]
    assert len(week) == 7
    assert week[6]["minutes"] == 10, "today is seeded"
    assert week[5]["minutes"] == 20, "yesterday is seeded"
    assert week[0]["minutes"] == 0, "6 days ago is NOT seeded -> must be zero"


async def test_dashboard_query_count_is_flat(sqlite_factory):
    """SELECT count must not scale with the number of streak rows/days."""
    engine, factory = sqlite_factory
    _, selects_with_2 = await _run_dashboard(engine, factory, days_filled=2)

    engine2 = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine2.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
    try:
        _, selects_with_30 = await _run_dashboard(engine2, factory2, days_filled=30)
    finally:
        await engine2.dispose()

    # streak dates + 7-day window + achievements
    # The lower bound is load-bearing: without it this test passes even when the
    # statement counter is silently dead (0 <= budget, and 0 == 0 for the
    # growth check below). A counter that never fires makes the test unfalsifiable.
    # Found by round-21 audit — see docs/optimization-report.md §23.
    assert 0 < selects_with_2 <= 4, (
        f"issued {selects_with_2} SELECTs; expected within budget "
        f"(the old today+per-day lookups would add 8 more)"
    )
    assert 0 < selects_with_30 <= 4, f"issued {selects_with_30} SELECTs; expected within budget"
    assert selects_with_30 == selects_with_2, (
        f"SELECT count changed ({selects_with_2} -> {selects_with_30})"
    )
