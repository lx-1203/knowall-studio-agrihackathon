"""Exam grading: per-question-type answer checking (round 33).

Why this file exists
-------------------
`test_core.py` only ever exercised `_check_answer` with `single_choice` questions.
Mutating the other branches left the whole suite green:

  * `multi_choice`  - flipping the option-set comparison to `!=`
  * `short_answer`  - lowering the keyword-overlap threshold from 0.5 to 0.0
  * `short_answer`  - deleting the "answers of one character are not accepted" guard
  * `calculation`   - widening the numeric tolerance from 0.01 to 100
  * `create_paper`  - ignoring the 20-question default when `total_questions` is absent

Those are the branches that decide whether a learner is marked right or wrong,
so they are worth pinning explicitly. The values below were measured, not guessed.
"""
import pytest


@pytest.fixture
def engine():
    from app.core.quiz import exam_engine
    return exam_engine


class TestMultiChoiceChecking:
    def test_option_order_does_not_matter(self, engine):
        """{A,B} == {B,A}: multi-choice answers are compared as sets."""
        assert engine._check_answer("AB", "BA", "multi_choice") is True
        assert engine._check_answer("B,A", "A,B", "multi_choice") is True

    def test_missing_option_is_wrong(self, engine):
        assert engine._check_answer("A", "AB", "multi_choice") is False
        assert engine._check_answer("ABC", "AB", "multi_choice") is False


class TestShortAnswerChecking:
    def test_identical_answer_is_correct(self, engine):
        assert engine._check_answer("DNA是遗传物质", "DNA是遗传物质", "short_answer") is True

    def test_half_the_keywords_is_enough_but_less_is_not(self, engine):
        """The threshold is an overlap of 0.5 against the CORRECT answer keywords.

        "DNA是遗传物质" -> keywords {dna, 是遗传物质}. Supplying one of the two is
        exactly 0.5 and passes; supplying an unrelated word is 0.0 and must fail.
        """
        assert engine._check_answer("是遗传物质", "DNA是遗传物质", "short_answer") is True
        assert engine._check_answer("遗传", "DNA是遗传物质", "short_answer") is False

    def test_exact_single_character_answers_match_but_partial_characters_do_not(self, engine):
        """Exact answers are valid; one-character fragments cannot win fuzzy credit."""
        assert engine._check_answer("A", "A", "short_answer") is True
        assert engine._check_answer("d", "d", "short_answer") is True
        assert engine._check_answer("A", "AB", "short_answer") is False
        assert engine._check_answer("d", "DNA", "short_answer") is False

    def test_empty_answer_is_wrong(self, engine):
        assert engine._check_answer("", "A", "short_answer") is False
        assert engine._check_answer("   ", "A", "short_answer") is False


class TestCalculationChecking:
    def test_small_numeric_difference_is_accepted(self, engine):
        """Tolerance is 0.01, so 3.14 vs 3.141 is right."""
        assert engine._check_answer("3.14", "3.141", "calculation") is True
        assert engine._check_answer("10", "10.005", "calculation") is True

    def test_difference_beyond_tolerance_is_wrong(self, engine):
        """0.06 apart is well outside a 0.01 tolerance."""
        assert engine._check_answer("3.2", "3.14", "calculation") is False
        assert engine._check_answer("1", "2", "calculation") is False


class TestCreatePaperDefaults:
    def _pool(self, n):
        return [
            {"id": f"q{i}", "question_type": "single_choice", "difficulty": "medium",
             "tags": [], "question_text": f"Q{i}", "answer": "A"}
            for i in range(n)
        ]

    def test_default_total_is_capped_at_twenty(self, engine):
        """With no `total_questions`, the paper takes min(20, len(pool))."""
        paper = engine.create_paper(self._pool(30), {"title": "默认卷"})
        assert len(paper["questions"]) == 20
        assert paper["total_score"] == 100  # 20 questions x 5 points

    def test_default_uses_the_whole_pool_when_it_is_smaller(self, engine):
        paper = engine.create_paper(self._pool(6), {"title": "小卷"})
        assert len(paper["questions"]) == 6
        assert len(paper["question_ids"]) == 6

    def test_paper_does_not_reuse_a_question(self, engine):
        paper = engine.create_paper(self._pool(30), {"title": "去重", "total_questions": 30})
        ids = paper["question_ids"]
        assert len(ids) == len(set(ids)) == 30
