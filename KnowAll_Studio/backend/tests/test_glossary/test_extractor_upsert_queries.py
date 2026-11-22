"""Query-count regression tests for sync_glossary_from_db()'s upsert stage.

Why this file exists
--------------------
After building the candidate terms, the function upserted them one at a time:

    for cand in merged_map.values():
        row = (await db.execute(select(GlossaryTerm).where(
            GlossaryTerm.subject == cand["subject"],
            GlossaryTerm.term == cand["term"],
        ))).scalars().first()

One SELECT per candidate term — and a single document can yield hundreds.

The refactor fetches the existing (subject, term) rows in one go. That is sound
because `merged_map` is keyed by `(subject, canonicalize(term))`, so the
`(subject, term)` pairs in it are necessarily unique — no in-batch collisions.

Harness note: this function reads KnowledgePointNode / KnowledgeSummary /
QuestionBank / Flashcard / Document before the upsert stage. Seeding only
`QuestionBank(question_type="term_definition")` rows is enough to produce N
candidates while keeping the other stages empty, which is what makes the
per-candidate cost measurable in isolation.
"""
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.glossary.extractor import sync_glossary_from_db
from app.database import Base
from app.models import GlossaryTerm, QuestionBank

SUBJECT = "测试学科"


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


async def _seed(factory, n_terms: int) -> None:
    """Seed n_terms term_definition questions -> n_terms candidates."""
    async with factory() as session:
        for i in range(n_terms):
            session.add(
                QuestionBank(
                    id=f"q_{i}",
                    question_type="term_definition",
                    question_text=f"术语{i}",
                    correct_answer=f"定义{i}",
                    tags=[],
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


async def _sync(engine, factory, n_terms: int, snapshot_path):
    await _seed(factory, n_terms)
    counter, detach = _record_selects(engine)
    try:
        async with factory() as session:
            result = await sync_glossary_from_db(
                session, subject=SUBJECT, snapshot_path=snapshot_path
            )
    finally:
        detach()

    async with factory() as session:
        stored = (
            await session.execute(
                select(func.count()).select_from(GlossaryTerm).where(GlossaryTerm.subject == SUBJECT)
            )
        ).scalar()
    return result, counter["n"], stored


async def test_sync_inserts_all_terms(sqlite_factory, tmp_path):
    """Functional check: every candidate becomes a GlossaryTerm row."""
    engine, factory = sqlite_factory
    result, _, stored = await _sync(engine, factory, n_terms=5, snapshot_path=tmp_path / "s.kgb")

    assert result["added"] == 5
    assert stored == 5


async def test_sync_is_idempotent(sqlite_factory, tmp_path):
    """Running twice must update, not duplicate."""
    engine, factory = sqlite_factory
    await _sync(engine, factory, n_terms=4, snapshot_path=tmp_path / "s.kgb")

    async with factory() as session:
        second = await sync_glossary_from_db(
            session, subject=SUBJECT, snapshot_path=tmp_path / "s.kgb"
        )

    async with factory() as session:
        stored = (
            await session.execute(
                select(func.count()).select_from(GlossaryTerm).where(GlossaryTerm.subject == SUBJECT)
            )
        ).scalar()

    assert second["added"] == 0
    assert second["updated"] == 4
    assert stored == 4, "second run must not duplicate rows"


async def test_sync_query_count_is_flat(sqlite_factory, tmp_path):
    """SELECT count must not scale with the number of candidate terms."""
    engine, factory = sqlite_factory
    _, small, _ = await _sync(engine, factory, n_terms=2, snapshot_path=tmp_path / "s.kgb")

    engine2 = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine2.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
    try:
        _, large, _ = await _sync(engine2, factory2, n_terms=60, snapshot_path=tmp_path / "s2.kgb")
    finally:
        await engine2.dispose()

    # The lower bound is load-bearing: without it this test passes even when the
    # statement counter is silently dead (0 <= budget, and 0 == 0 for the
    # growth check below). A counter that never fires makes the test unfalsifiable.
    # Found by round-21 audit — see docs/optimization-report.md §23.
    assert 0 < large <= 12, (
        f"60 candidates issued {large} SELECTs; expected within budget "
        f"(the old per-candidate lookup would add 60)"
    )
    assert large == small, (
        f"SELECT count grew with candidate count ({small} -> {large}): "
        f"the per-candidate GlossaryTerm lookup is back inside the loop"
    )
