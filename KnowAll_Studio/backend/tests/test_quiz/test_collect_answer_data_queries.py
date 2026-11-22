"""Query-count regression tests for MasteryAnalyzer._collect_answer_data().

Why this file exists
--------------------
The method joins answer records to questions, then falls back to the coverage
table for any record without `knowledge_point_ids` — and it did that fallback
with one query *per such record*, i.e. O(records) round trips on the main
analysis path (analyze() -> _collect_answer_data()). It is now one IN query.

Note on evidence: unlike the other query-count tests added this session, the fix
here was applied before this test was written, so the "before" number is
analytic (1 + N) rather than a captured failing run. The test still locks the
property in from here on.
"""
from collections import defaultdict

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.quiz.mastery_analyzer import MasteryAnalyzer
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


async def _seed(factory, n_records: int, with_kp_ids: bool) -> None:
    """Seed answers; when with_kp_ids is False they rely on the coverage table."""
    async with factory() as session:
        for i in range(n_records):
            qid = f"q_{i}"
            session.add(
                QuestionBank(
                    id=qid,
                    question_type="single_choice",
                    question_text=f"题目{i}",
                    correct_answer="A",
                    tags=["生物"],
                )
            )
            session.add(
                AnswerRecord(
                    id=f"a_{i}",
                    user_id="u1",
                    question_id=qid,
                    user_answer="B",
                    is_correct=False,
                    knowledge_point_ids=[f"kp_{i}"] if with_kp_ids else None,
                )
            )
            session.add(
                KnowledgeCoverage(
                    id=f"cov_{i}",
                    knowledge_point_id=f"kp_cov_{i}",
                    resource_type="question",
                    resource_id=qid,
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


async def _collect(engine, factory, n_records: int, with_kp_ids: bool):
    await _seed(factory, n_records, with_kp_ids)
    counter, detach = _record_selects(engine)
    try:
        async with factory() as session:
            data = await MasteryAnalyzer()._collect_answer_data(session, "u1")
    finally:
        detach()
    return data, counter["n"]


async def test_collect_uses_coverage_fallback(sqlite_factory):
    """Functional check: the coverage table is still consulted and honoured."""
    engine, factory = sqlite_factory
    data, _ = await _collect(engine, factory, n_records=3, with_kp_ids=False)

    assert set(data.keys()) == {"kp_cov_0", "kp_cov_1", "kp_cov_2"}
    assert all(len(v) == 1 for v in data.values())


async def test_collect_prefers_record_kp_ids(sqlite_factory):
    """When the record carries kp ids, the coverage fallback is not needed."""
    engine, factory = sqlite_factory
    data, selects = await _collect(engine, factory, n_records=3, with_kp_ids=True)

    assert set(data.keys()) == {"kp_0", "kp_1", "kp_2"}
    # No record needs the fallback, so only the join query runs.
    assert selects == 1, f"expected 1 SELECT when no fallback is needed, got {selects}"


async def test_collect_query_count_is_flat(sqlite_factory):
    """SELECT count must not scale with the number of fallback lookups."""
    engine, factory = sqlite_factory
    _, small = await _collect(engine, factory, n_records=2, with_kp_ids=False)

    engine2 = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine2.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
    try:
        _, large = await _collect(engine2, factory2, n_records=60, with_kp_ids=False)
    finally:
        await engine2.dispose()

    # The lower bound is load-bearing: without it this test passes even when the
    # statement counter is silently dead (0 <= budget, and 0 == 0 for the
    # growth check below). A counter that never fires makes the test unfalsifiable.
    # Found by round-21 audit — see docs/optimization-report.md §23.
    assert 0 < small <= 2, f"2 records issued {small} SELECTs; expected within budget (join + batched fallback)"
    assert 0 < large <= 2, f"60 records issued {large} SELECTs; expected within budget (was 1 + 60 before)"
    assert large == small, (
        f"SELECT count grew with record count ({small} -> {large}): "
        f"the per-record coverage lookup is back inside the loop"
    )
