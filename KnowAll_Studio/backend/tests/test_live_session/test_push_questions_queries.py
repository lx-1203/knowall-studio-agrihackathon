"""Query-count regression tests for POST /live/sessions/{id}/push (push_questions).

Why this file exists
--------------------
The endpoint validated the submitted question ids one at a time:

    for i, qid in enumerate(req.question_ids):
        q_result = await db.execute(select(QuestionBank).where(QuestionBank.id == qid))

`PushQuestionRequest` caps question_ids at 50, so this is bounded — but it is
still up to 50 round trips on a user-triggered endpoint. It is now one IN query.

Important subtlety preserved: `question_index` is `max_idx + i + 1` where `i` is
the index in the SUBMITTED list, including ids that turn out not to exist. So a
missing question still leaves a gap in the numbering. The functional test pins
that behaviour.
"""
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.live_session import PushQuestionRequest, push_questions
from app.database import Base
from app.models import LiveQuestion, LiveSession, QuestionBank


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


async def _seed(factory, n_questions: int) -> None:
    """Seed a session hosted by 'local_user' plus n_questions questions."""
    async with factory() as session:
        session.add(
            LiveSession(
                id="s1",
                host_id="local_user",
                title="测试会话",
                room_code="ABC123",
            )
        )
        for i in range(n_questions):
            session.add(
                QuestionBank(
                    id=f"q_{i}",
                    question_type="single_choice",
                    question_text=f"题目{i}",
                    correct_answer="A",
                    options=[{"label": "A", "text": "选项A"}],
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


async def _push(engine, factory, n_questions: int, ids=None):
    await _seed(factory, n_questions)
    question_ids = ids if ids is not None else [f"q_{i}" for i in range(n_questions)]
    counter, detach = _record_selects(engine)
    try:
        async with factory() as session:
            result = await push_questions(
                session_id="s1",
                req=PushQuestionRequest(question_ids=question_ids),
                db=session,
                current_user=None,  # -> get_user_id(None) == "local_user"
            )
    finally:
        detach()

    async with factory() as session:
        from sqlalchemy import select
        rows = (await session.execute(select(LiveQuestion).order_by(LiveQuestion.question_index))).scalars().all()
    return result, counter["n"], [(r.question_id, r.question_index) for r in rows]


async def test_push_questions_creates_rows(sqlite_factory):
    """Functional check: rows are created in order with sequential indices."""
    engine, factory = sqlite_factory
    result, _, rows = await _push(engine, factory, n_questions=3)

    assert result["pushed_count"] == 3
    assert rows == [("q_0", 1), ("q_1", 2), ("q_2", 3)]


async def test_push_questions_numbering_gap_for_missing_ids(sqlite_factory):
    """A non-existent id must still consume an index, as before the refactor."""
    engine, factory = sqlite_factory
    # q_1 does not exist -> only 2 rows, numbered 1 and 3
    result, _, rows = await _push(engine, factory, n_questions=3, ids=["q_0", "missing", "q_2"])

    assert result["pushed_count"] == 2
    assert rows == [("q_0", 1), ("q_2", 3)], "index must reflect the submitted position, not a running counter"


async def test_push_questions_query_count_is_flat(sqlite_factory):
    """SELECT count must not scale with the number of submitted ids."""
    engine, factory = sqlite_factory
    _, small, _ = await _push(engine, factory, n_questions=2)

    engine2 = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine2.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
    try:
        _, large, _ = await _push(engine2, factory2, n_questions=40)
    finally:
        await engine2.dispose()

    # The lower bound is load-bearing: without it this test passes even when the
    # statement counter is silently dead (0 <= budget, and 0 == 0 for the
    # growth check below). A counter that never fires makes the test unfalsifiable.
    # Found by round-21 audit — see docs/optimization-report.md §23.
    assert 0 < small <= 3, (
        f"2 ids issued {small} SELECTs; expected within budget "
        f"(session + max-index + batched question lookup)"
    )
    assert 0 < large <= 3, (
        f"40 ids issued {large} SELECTs; expected within budget "
        f"(the old per-id lookup would issue 2 + 40 = 42)"
    )
    assert large == small, (
        f"SELECT count grew with id count ({small} -> {large}): "
        f"the per-id QuestionBank lookup is back inside the loop"
    )
