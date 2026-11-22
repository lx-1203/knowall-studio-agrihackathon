"""Query-count regression tests for FeedbackEngine.scan().

Why this file exists
--------------------
`scan()` walks every weak knowledge point and, for each one, issued two
separate SELECTs — one for its KnowledgeCoverage rows and one to check whether
it was already queued. That is 2 + 2*N queries for N weak points, and N grows
with how much a user has studied, so the endpoint gets slower the more the user
uses it.

These tests assert on the **number of SQL statements**, not wall-clock time, so
they are deterministic and CI-safe. The count must not grow with the number of
weak knowledge points.
"""
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.memory import feedback_loop as fl_module
from app.core.memory.feedback_loop import FeedbackEngine
from app.database import Base
from app.models import AnswerRecord, KnowledgeCoverage, QuestionBank


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


async def _seed(factory, n_weak: int) -> None:
    """Seed n_weak weak knowledge points (accuracy 0), each with coverage rows."""
    async with factory() as session:
        for i in range(n_weak):
            kp_id = f"kp_{i}"
            q_id = f"q_{i}"
            session.add(
                QuestionBank(
                    id=q_id,
                    question_type="single_choice",
                    question_text=f"题目{i}",
                    correct_answer="A",
                )
            )
            # accuracy 0 -> below every tier threshold -> "weak"
            session.add(
                AnswerRecord(
                    id=f"a_{i}",
                    user_id="u1",
                    question_id=q_id,
                    user_answer="B",
                    is_correct=False,
                    knowledge_point_ids=[kp_id],
                )
            )
            session.add(
                KnowledgeCoverage(
                    id=f"cov_{i}",
                    knowledge_point_id=kp_id,
                    resource_type="question",
                    resource_id=q_id,
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


async def _run_scan(engine, factory, monkeypatch, n_weak: int):
    await _seed(factory, n_weak)
    monkeypatch.setattr(fl_module, "async_session", factory)
    counter, detach = _record_selects(engine)
    try:
        result = await FeedbackEngine().scan(user_id="u1")
    finally:
        detach()
    return result, counter["n"]


async def test_scan_still_pushes_weak_points(sqlite_factory, monkeypatch):
    """Functional check: weak KPs are detected and queued."""
    engine, factory = sqlite_factory
    result, _ = await _run_scan(engine, factory, monkeypatch, n_weak=3)

    assert result["total_scanned"] == 3
    assert result["weak_found"] == 3
    assert result["pushed_to_queue"] == 3, "one coverage row per weak KP should be queued"


async def test_scan_query_count_is_constant(sqlite_factory, monkeypatch):
    """SELECT count must not scale with the number of weak knowledge points."""
    engine, factory = sqlite_factory
    _, selects_small = await _run_scan(engine, factory, monkeypatch, n_weak=2)

    engine2 = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine2.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
    try:
        _, selects_large = await _run_scan(engine2, factory2, monkeypatch, n_weak=40)
    finally:
        await engine2.dispose()

    # The lower bound is load-bearing: without it this test passes even when the
    # statement counter is silently dead (0 <= budget, and 0 == 0 for the
    # growth check below). A counter that never fires makes the test unfalsifiable.
    # Found by round-21 audit — see docs/optimization-report.md §23.
    assert 0 < selects_small <= 4, (
        f"2 weak KPs issued {selects_small} SELECTs; expected within budget "
        f"(the old per-KP lookups would issue 2 + 2*2 = 6)"
    )
    assert 0 < selects_large <= 4, (
        f"40 weak KPs issued {selects_large} SELECTs; expected within budget "
        f"(the old per-KP lookups would issue 2 + 2*40 = 82)"
    )
    assert selects_large == selects_small, (
        f"SELECT count grew with the number of weak KPs ({selects_small} -> {selects_large}): "
        f"the per-KP lookups are still inside the loop"
    )
