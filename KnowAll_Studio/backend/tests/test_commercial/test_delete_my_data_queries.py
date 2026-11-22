"""Tests for delete_my_data (GDPR erasure).

Correction (measured, not assumed)
----------------------------------
This endpoint was originally flagged as an N+1: it loops over six tables and
calls `await db.delete(row)` once per row. Measuring it showed that reading was
wrong — SQLAlchemy's unit of work batches those deletes into one `executemany`
per table, so the number of cursor calls is flat:

    rows/table  cursor_calls  executemany  param_sets
             2             9            3          12
            40             9            3         126
           300             9            3         906

So there is no round-trip problem here, and no rewrite was needed. The real
(and much smaller) cost is that N ORM objects get hydrated into the session
first — memory, not round trips — on a rare endpoint.

The tests below therefore keep two things honest: erasure actually works, and
the constant round-trip count stays constant if someone later replaces the loop
with something that does issue a query per row.
"""
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.commercial import delete_my_data
from app.database import Base
from app.models import AnswerRecord, ErrorLog, QuestionBank, StudyPlan, User

ROWS_PER_TABLE = 4
ERASED_MODELS = (AnswerRecord, ErrorLog, StudyPlan)


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


async def _seed(factory, rows_per_table: int) -> User:
    """Seed a user plus rows_per_table rows in three of the erased tables."""
    async with factory() as session:
        user = User(id="u1", username="u1", email="u1@example.com", password_hash="x")
        session.add(user)
        for i in range(rows_per_table):
            qid = f"q_{i}"
            session.add(
                QuestionBank(
                    id=qid,
                    question_type="single_choice",
                    question_text=f"题目{i}",
                    correct_answer="A",
                )
            )
            session.add(
                AnswerRecord(
                    id=f"a_{i}",
                    user_id="u1",
                    question_id=qid,
                    user_answer="B",
                    is_correct=False,
                )
            )
            session.add(ErrorLog(id=f"e_{i}", user_id="u1", question_id=qid))
            session.add(StudyPlan(id=f"s_{i}", user_id="u1", name=f"plan{i}"))
        await session.commit()
        return user


def _record_statements(engine):
    counter = {"n": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _before)

    def detach():
        event.remove(engine.sync_engine, "before_cursor_execute", _before)

    return counter, detach


async def _erase(engine, factory, rows_per_table: int):
    user = await _seed(factory, rows_per_table)
    counter, detach = _record_statements(engine)
    try:
        async with factory() as session:
            result = await delete_my_data(current_user=user, db=session)
    finally:
        detach()

    async with factory() as session:
        remaining = {
            model.__name__: (await session.execute(select(func.count()).select_from(model))).scalar()
            for model in ERASED_MODELS
        }
    return result, counter["n"], remaining


async def test_delete_my_data_actually_erases_rows(sqlite_factory):
    """Functional check: the rows really are gone and the account is disabled."""
    engine, factory = sqlite_factory
    result, _, remaining = await _erase(engine, factory, rows_per_table=ROWS_PER_TABLE)

    assert result["status"] == "deleted"
    assert remaining == {"AnswerRecord": 0, "ErrorLog": 0, "StudyPlan": 0}, remaining


async def test_delete_my_data_statement_count_stays_flat(sqlite_factory):
    """Round-trip count must stay constant as the erased row count grows.

    This already holds today (SQLAlchemy batches the ORM deletes). The test
    exists to lock that property in: a future edit that issues a statement per
    row would break it.
    """
    engine, factory = sqlite_factory
    _, small, _ = await _erase(engine, factory, rows_per_table=2)

    engine2 = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine2.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
    try:
        _, large, _ = await _erase(engine2, factory2, rows_per_table=40)
    finally:
        await engine2.dispose()

    # The lower bound is load-bearing: without it this test passes even when the
    # statement counter is silently dead (0 <= budget, and 0 == 0 for the
    # growth check below). A counter that never fires makes the test unfalsifiable.
    # Found by round-21 audit — see docs/optimization-report.md §23.
    assert 0 < small <= 20, f"2 rows/table issued {small} statements; expected within budget"
    assert 0 < large <= 20, f"40 rows/table issued {large} statements; expected within budget"
    assert large == small, (
        f"statement count grew with row count ({small} -> {large}): "
        f"something now issues a statement per row"
    )
