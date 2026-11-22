"""Query-count regression tests for MasteryAnalyzer.get_answer_history().

Why this file exists
--------------------
get_answer_history() backs a paginated answer-history view. It used to resolve
each record's question and knowledge-point titles with *one query per record* —
a classic N+1. With page_size=10 and knowledge points present that is
2 + 2*10 = 22 SELECTs for 10 rows; page_size=100 would issue 202.

These tests assert on the **number of SQL statements executed**, not on
wall-clock time, so they are deterministic and safe for CI. The count must not
grow with page_size — that is the property that distinguishes a batched
implementation from an N+1 one.
"""
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.quiz import mastery_analyzer as ma_module
from app.core.quiz.mastery_analyzer import MasteryAnalyzer
from app.database import Base
from app.models import AnswerRecord, KnowledgePointNode, QuestionBank

KP_COUNT = 3


@pytest.fixture
async def sqlite_factory():
    """In-memory SQLite with all tables created, plus a sessionmaker."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield engine, factory
    finally:
        await engine.dispose()


async def _seed(factory, n_records: int) -> None:
    """Seed n_records answer records, each linked to an existing question + KP."""
    async with factory() as session:
        kp_ids = []
        for i in range(KP_COUNT):
            kp_id = f"kp_{i}"
            session.add(
                KnowledgePointNode(
                    id=kp_id,
                    document_id="doc1",
                    level=1,
                    sequence=i,
                    title=f"知识点标题{i}",
                    explanation=f"知识点解释{i}",
                )
            )
            kp_ids.append(kp_id)
        for i in range(n_records):
            q_id = f"q_{i}"
            session.add(
                QuestionBank(
                    id=q_id,
                    question_type="single_choice",
                    question_text=f"题目正文{i}",
                    correct_answer="A",
                )
            )
            session.add(
                AnswerRecord(
                    id=f"a_{i}",
                    user_id="u1",
                    question_id=q_id,
                    user_answer="A",
                    is_correct=(i % 2 == 0),
                    knowledge_point_ids=[kp_ids[i % KP_COUNT]],
                )
            )
        await session.commit()


def _record_selects(engine):
    """Attach a listener counting SELECT statements; returns (counter, detach)."""
    counter = {"n": 0}

    def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _before_cursor_execute)

    def detach():
        event.remove(engine.sync_engine, "before_cursor_execute", _before_cursor_execute)

    return counter, detach


async def _run_history(engine, factory, monkeypatch, page_size: int):
    """Seed page_size records and return (result, select_count)."""
    await _seed(factory, page_size)
    monkeypatch.setattr(ma_module, "async_session", factory)
    counter, detach = _record_selects(engine)
    try:
        result = await MasteryAnalyzer().get_answer_history(user_id="u1", page_size=page_size)
    finally:
        detach()
    return result, counter["n"]


async def test_answer_history_returns_enriched_items(sqlite_factory, monkeypatch):
    """Enrichment must still work: question text and KP titles are populated."""
    engine, factory = sqlite_factory
    result, _ = await _run_history(engine, factory, monkeypatch, page_size=10)

    assert result["total"] == 10
    assert len(result["items"]) == 10

    item = result["items"][0]
    assert item["question_text"].startswith("题目正文")
    assert item["correct_answer"] == "A"
    assert item["knowledge_point_ids"], "seeded record must carry knowledge point ids"
    assert item["knowledge_point_titles"], "KP titles must be resolved, not silently empty"
    assert item["knowledge_point_titles"][0].startswith("知识点标题")


async def test_answer_history_query_count_is_constant(sqlite_factory, monkeypatch):
    """SELECT count must not scale with page_size (no N+1).

    A batched implementation needs a fixed number of statements:
    count + page fetch + batched questions + batched KP titles.
    """
    engine, factory = sqlite_factory
    _, selects_small = await _run_history(engine, factory, monkeypatch, page_size=5)

    engine2 = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine2.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
    try:
        _, selects_large = await _run_history(engine2, factory2, monkeypatch, page_size=25)
    finally:
        await engine2.dispose()

    # The lower bound is load-bearing: without it this test passes even when the
    # statement counter is silently dead (0 <= budget, and 0 == 0 for the
    # growth check below). A counter that never fires makes the test unfalsifiable.
    # Found by round-21 audit — see docs/optimization-report.md §23.
    assert 0 < selects_small <= 4, (
        f"page_size=5 issued {selects_small} SELECTs; expected within budget "
        f"(an N+1 would issue 2 + 2*5 = 12)"
    )
    assert 0 < selects_large <= 4, (
        f"page_size=25 issued {selects_large} SELECTs; expected within budget "
        f"(an N+1 would issue 2 + 2*25 = 52)"
    )
    assert selects_large == selects_small, (
        f"SELECT count grew with page_size ({selects_small} -> {selects_large}): "
        f"the per-record lookup is still inside the loop"
    )
