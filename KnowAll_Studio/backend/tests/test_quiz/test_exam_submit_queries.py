"""Query-count regression tests for POST /exam/submit (submit_exam).

Why this file exists
--------------------
Grading a paper saves one AnswerRecord per question and, for every *wrong*
answer, looked up the existing ErrorLog row individually — one SELECT per wrong
answer. A 50-question paper with 30 wrong answers therefore issued 30 extra
queries on a hot, user-facing path. The lookup is now one batched IN query.

These tests assert on the **number of SQL statements**, not wall-clock time, so
they are deterministic and CI-safe. The count must not grow with the number of
wrong answers.
"""
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api import questions as q_module
from app.api.questions import SubmitExamRequest, submit_exam
from app.database import Base
from app.models import ErrorLog, ExamPaper, QuestionBank


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


async def _seed_paper(factory, n_questions: int) -> str:
    """One paper with n_questions questions; returns paper_id."""
    paper_id = "paper1"
    q_ids = [f"q_{i}" for i in range(n_questions)]
    async with factory() as session:
        for i, qid in enumerate(q_ids):
            session.add(
                QuestionBank(
                    id=qid,
                    question_type="single_choice",
                    question_text=f"题目{i}",
                    correct_answer="A",
                    cognitive_level="L2_understand",
                )
            )
        session.add(ExamPaper(id=paper_id, title="测试卷", question_ids=q_ids))
        await session.commit()
    return paper_id


def _fake_grader(n_wrong: int):
    """Stand-in for exam_engine.grade_enhanced: n_wrong incorrect answers."""
    async def grade_enhanced(paper, answers, enable_semantic=True, model=None):
        return {
            "score": 0,
            "total": n_wrong,
            "details": [
                {
                    "question_id": f"q_{i}",
                    "user_answer": "B",
                    "is_correct": False,
                }
                for i in range(n_wrong)
            ],
        }
    return grade_enhanced


def _record_selects(engine):
    counter = {"n": 0}

    def _before(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _before)

    def detach():
        event.remove(engine.sync_engine, "before_cursor_execute", _before)

    return counter, detach


async def _submit(engine, factory, monkeypatch, n_wrong: int):
    paper_id = await _seed_paper(factory, n_wrong)
    monkeypatch.setattr(q_module, "exam_engine", SimpleNamespace(grade_enhanced=_fake_grader(n_wrong)))

    counter, detach = _record_selects(engine)
    try:
        async with factory() as session:
            result = await submit_exam(
                SubmitExamRequest(paper_id=paper_id, answers={}),
                db=session,
                current_user=None,
            )
    finally:
        detach()

    # ErrorLog rows must still have been created for every wrong answer.
    async with factory() as session:
        from sqlalchemy import select
        rows = (await session.execute(select(ErrorLog))).scalars().all()
    return result, counter["n"], len(rows)


async def test_submit_creates_error_logs(sqlite_factory, monkeypatch):
    """Functional check: every wrong answer produces exactly one ErrorLog row."""
    engine, factory = sqlite_factory
    result, _, n_error_logs = await _submit(engine, factory, monkeypatch, n_wrong=5)

    assert len(result["details"]) == 5
    assert n_error_logs == 5, f"expected 5 ErrorLog rows, got {n_error_logs}"


async def test_submit_query_count_is_constant(sqlite_factory, monkeypatch):
    """SELECT count must not scale with the number of wrong answers."""
    engine, factory = sqlite_factory
    _, selects_small, _ = await _submit(engine, factory, monkeypatch, n_wrong=2)

    engine2 = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine2.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory2 = async_sessionmaker(engine2, class_=AsyncSession, expire_on_commit=False)
    try:
        _, selects_large, _ = await _submit(engine2, factory2, monkeypatch, n_wrong=30)
    finally:
        await engine2.dispose()

    # The lower bound is load-bearing: without it this test passes even when the
    # statement counter is silently dead (0 <= budget, and 0 == 0 for the
    # growth check below). A counter that never fires makes the test unfalsifiable.
    # Found by round-21 audit — see docs/optimization-report.md §23.
    assert 0 < selects_small <= 6, (
        f"2 wrong answers issued {selects_small} SELECTs; expected within budget "
        f"(the old per-answer lookup would issue 3 + 2 = 5)"
    )
    assert 0 < selects_large <= 6, (
        f"30 wrong answers issued {selects_large} SELECTs; expected within budget "
        f"(the old per-answer lookup would issue 3 + 30 = 33)"
    )
    assert selects_large == selects_small, (
        f"SELECT count grew with the number of wrong answers ({selects_small} -> {selects_large}): "
        f"the per-answer ErrorLog lookup is still inside the loop"
    )
