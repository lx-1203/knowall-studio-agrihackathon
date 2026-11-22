"""Regression tests for CoverageEngine.calculate() (round 24).

Two defects were found here by mutation/audit work, both invisible to the mocks
that the older tests in test_coverage.py use:

1. **N+1.** `calculate()` called `self._get_accuracy(node.id, session)` once per
   knowledge point, and that helper ran two queries per call. Measured with a
   statement counter: 2 points -> 5 SELECTs, 30 points -> 33. The count grew
   with the summary size. It is now one batched join/group-by: 4 SELECTs flat.

2. **`weak_points` was always empty.** The accuracy aggregate summed
   `AnswerRecord.is_correct.cast(int)`; the Python built-in `int` is not a
   SQLAlchemy type, so building the expression raised
   "int object has no attribute _isnull" on *every* call, and the caller
   swallowed it. test_coverage.py patched `_type_from_args` to work around it,
   which is why CI stayed green. The portable CASE expression fixes it.

These tests use a real in-memory SQLite session (no mocks) so neither defect can
be mocked away again. They assert on the **number of SQL statements**, not on
wall-clock time, so they are deterministic and CI-safe.
"""
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.memory import coverage as cov_module
from app.core.memory.coverage import CoverageEngine
from app.database import Base
from app.models import AnswerRecord, KnowledgeCoverage, KnowledgePointNode, QuestionBank


async def _seed(factory, n_points: int, *, correct_answers: bool):
    async with factory() as session:
        for i in range(n_points):
            session.add(KnowledgePointNode(
                id=f"kp{i}", summary_id="sum1", document_id="doc1",
                level=1, sequence=i, title=f"Topic {i}", explanation=f"Explanation {i}",
            ))
            session.add(QuestionBank(
                id=f"q{i}", question_type="single_choice",
                question_text=f"Question {i}", correct_answer="A",
            ))
            session.add(KnowledgeCoverage(
                knowledge_point_id=f"kp{i}", resource_type="question", resource_id=f"q{i}",
            ))
            session.add(AnswerRecord(
                id=f"a{i}", question_id=f"q{i}", user_answer="A" if correct_answers else "B",
                is_correct=correct_answers,
            ))
        await session.commit()


async def _engine_and_counter(n_points: int, *, correct_answers: bool = False):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _seed(factory, n_points, correct_answers=correct_answers)

    counter = {"n": 0}

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    return engine, factory, counter


async def _run(n_points: int, *, correct_answers: bool = False):
    engine, factory, counter = await _engine_and_counter(n_points, correct_answers=correct_answers)
    cov_module.async_session = factory
    try:
        report = await CoverageEngine().calculate("sum1")
    finally:
        await engine.dispose()
    return counter["n"], report


@pytest.mark.parametrize("n_points", [2, 30])
async def test_calculate_query_count_does_not_grow(n_points):
    """4 SELECTs whether the summary has 2 knowledge points or 30."""
    selects, report = await _run(n_points)
    assert report["total_knowledge_points"] == n_points
    # The lower bound is load-bearing: without it this passes even when the
    # statement counter is silently dead (see docs/optimization-report.md §23).
    assert 0 < selects <= 4, f"{n_points} knowledge points issued {selects} SELECTs"


async def test_calculate_reports_weak_points_without_any_patch():
    """`weak_points` must be populated from real data.

    This is the assertion the old suite could not make: it patched
    `_type_from_args` to keep the broken `cast(int)` aggregate from raising, so
    an accuracy that always failed still looked like a passing test.
    """
    _, report = await _run(3)
    assert len(report["weak_points"]) == 3, "all three answers are wrong -> all weak"
    assert report["weak_points"][0]["accuracy"] == 0.0
    assert report["weak_points"][0]["id"] == "kp0"


async def test_calculate_does_not_flag_correct_answers_as_weak():
    """A perfect score must produce no weak points (guards the threshold logic)."""
    _, report = await _run(3, correct_answers=True)
    assert report["weak_points"] == [], "accuracy 1.0 must not be flagged as weak"


async def test_calculate_accuracy_is_computed_not_defaulted():
    """Half right -> accuracy 0.5, which is below the 0.7 threshold."""
    engine, factory, _ = await _engine_and_counter(0)
    async with factory() as session:
        session.add(KnowledgePointNode(id="kp", summary_id="s", document_id="d",
                                       level=1, sequence=1, title="T", explanation="E"))
        for i in range(2):
            session.add(QuestionBank(id=f"q{i}", question_type="single_choice",
                                     question_text=f"Q{i}", correct_answer="A"))
            session.add(KnowledgeCoverage(knowledge_point_id="kp", resource_type="question",
                                          resource_id=f"q{i}"))
        session.add(AnswerRecord(id="a0", question_id="q0", user_answer="A", is_correct=True))
        session.add(AnswerRecord(id="a1", question_id="q1", user_answer="B", is_correct=False))
        await session.commit()
    cov_module.async_session = factory
    try:
        report = await CoverageEngine().calculate("s")
    finally:
        await engine.dispose()
    assert len(report["weak_points"]) == 1
    assert report["weak_points"][0]["accuracy"] == 0.5


# ============================================================
# Round 28: three more behaviours in calculate() that no test could fail on.
#
# Found by mutating calculate() again (round 24 only pinned the query count and
# weak_points). The surviving mutations were:
#
#   * `uncovered` used `and` -> `or`  (partial coverage never exercised: the only
#     uncovered assertions were the empty list and a node missing from BOTH);
#   * `full_coverage_rate` rounded to 2 instead of 4 decimals (only `== 0.0`
#     was ever asserted, and 0.0 has no decimals to lose);
#   * the weak threshold moved 0.7 -> 0.8 (only fed 0.55 and 1.0, both outside
#     the 0.7-0.8 band, so the boundary itself was never pinned).
#
# A fourth mutation, `if total and total > 0` -> `if total is not None`, is an
# EQUIVALENT mutant: the query is an INNER JOIN, so every returned row has
# count >= 1 and the two conditions agree. There is no behaviour to detect, so
# no test is added for it - chasing equivalent mutants adds noise, not safety.
# ============================================================


async def _seed_graph(factory, n_nodes: int, question_kps=(), flashcard_kps=(), answers=None):
    """Seed nodes plus coverage, with `answers = {kp_id: (total, n_correct)}`."""
    answers = answers or {}
    async with factory() as session:
        for i in range(n_nodes):
            session.add(KnowledgePointNode(
                id=f"kp{i}", summary_id="s", document_id="d",
                level=1, sequence=i, title=f"Topic {i}", explanation=f"E{i}",
            ))
        for kp in question_kps:
            n_q = max(1, answers.get(kp, (1, 0))[0])
            for j in range(n_q):
                session.add(QuestionBank(id=f"{kp}_q{j}", question_type="single_choice",
                                         question_text="Q", correct_answer="A"))
                session.add(KnowledgeCoverage(knowledge_point_id=kp,
                                              resource_type="question",
                                              resource_id=f"{kp}_q{j}"))
        for kp in flashcard_kps:
            session.add(KnowledgeCoverage(knowledge_point_id=kp,
                                          resource_type="flashcard",
                                          resource_id=f"{kp}_f0"))
        for kp, (total, n_correct) in answers.items():
            for j in range(total):
                session.add(AnswerRecord(id=f"{kp}_a{j}", question_id=f"{kp}_q{j}",
                                         user_answer="A", is_correct=(j < n_correct)))
        await session.commit()


async def _report_for(tmp_path_unused, *, n_nodes, question_kps=(), flashcard_kps=(), answers=None):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await _seed_graph(factory, n_nodes, question_kps, flashcard_kps, answers)
    cov_module.async_session = factory
    try:
        return await CoverageEngine().calculate("s")
    finally:
        await engine.dispose()


async def test_uncovered_requires_missing_from_both_question_and_flashcard():
    """A point covered by only ONE resource type is NOT uncovered.

    The old assertions only ever had the empty list or a node missing from both,
    so flipping `and` to `or` in the uncovered test changed nothing observable.
    """
    report = await _report_for(None, n_nodes=2, question_kps=["kp0"])
    assert report["total_knowledge_points"] == 2
    assert report["covered_by_questions"] == 1
    assert [u["id"] for u in report["uncovered_points"]] == ["kp1"], (
        "kp0 has question coverage, so it is not uncovered"
    )


async def test_full_coverage_rate_keeps_four_decimal_places():
    """1 of 3 points fully covered -> 0.3333, not 0.33.

    Only `== 0.0` was asserted before, and zero cannot reveal lost precision.
    """
    report = await _report_for(
        None, n_nodes=3, question_kps=["kp0", "kp1", "kp2"], flashcard_kps=["kp0"]
    )
    assert report["full_coverage"] == 1
    assert report["full_coverage_rate"] == 0.3333


async def test_weak_threshold_boundary_is_seventy_percent():
    """0.75 is NOT weak; 0.65 IS weak - that pins the threshold at 0.7.

    The old tests fed 0.55 and 1.0, both outside the 0.7-0.8 band, so moving the
    threshold to 0.8 was invisible.
    """
    report = await _report_for(
        None,
        n_nodes=2,
        question_kps=["kp0", "kp1"],
        answers={"kp0": (4, 3), "kp1": (20, 13)},  # 0.75 and 0.65
    )
    weak = {w["id"]: w["accuracy"] for w in report["weak_points"]}
    assert weak == {"kp1": 0.65}, f"expected only kp1 (0.65) below 0.7, got {weak}"