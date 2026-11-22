"""Coverage engine - calculates knowledge point coverage by questions and flashcards"""
import logging
from sqlalchemy import case, select, func
from app.database import async_session
from app.models import KnowledgePointNode, KnowledgeCoverage, AnswerRecord

logger = logging.getLogger(__name__)


class CoverageEngine:
    """Calculates how well knowledge points are covered by questions and flashcards."""

    async def calculate(self, summary_id: str, user_id: str = "local_user") -> dict:
        """Calculate coverage report for a knowledge summary.

        Returns:
            Coverage report dict with total, covered, rates, uncovered, weak points
        """
        async with async_session() as session:
            # Get all knowledge points for this summary
            stmt = select(KnowledgePointNode).where(
                KnowledgePointNode.summary_id == summary_id
            )
            result = await session.execute(stmt)
            all_nodes = result.scalars().all()

            total_kp = len(all_nodes)
            if total_kp == 0:
                return {
                    "total_knowledge_points": 0,
                    "covered_by_questions": 0,
                    "covered_by_flashcards": 0,
                    "full_coverage": 0,
                    "coverage_rate_questions": 0.0,
                    "coverage_rate_flashcards": 0.0,
                    "full_coverage_rate": 0.0,
                    "uncovered_points": [],
                    "weak_points": [],
                }

            # Count coverage
            kp_ids = [n.id for n in all_nodes]

            # Questions coverage
            q_stmt = select(
                KnowledgeCoverage.knowledge_point_id,
                func.count().label("count"),
            ).where(
                KnowledgeCoverage.knowledge_point_id.in_(kp_ids),
                KnowledgeCoverage.resource_type == "question",
            ).group_by(KnowledgeCoverage.knowledge_point_id)
            q_result = await session.execute(q_stmt)
            question_coverage = {row[0]: row[1] for row in q_result.fetchall()}

            # Flashcards coverage
            f_stmt = select(
                KnowledgeCoverage.knowledge_point_id,
                func.count().label("count"),
            ).where(
                KnowledgeCoverage.knowledge_point_id.in_(kp_ids),
                KnowledgeCoverage.resource_type == "flashcard",
            ).group_by(KnowledgeCoverage.knowledge_point_id)
            f_result = await session.execute(f_stmt)
            flashcard_coverage = {row[0]: row[1] for row in f_result.fetchall()}

            # Calculate statistics
            covered_by_q = len(question_coverage)
            covered_by_f = len(flashcard_coverage)
            full_coverage = len(set(question_coverage.keys()) & set(flashcard_coverage.keys()))

            # Find uncovered points
            uncovered = []
            weak = []

            # One query for every knowledge point, instead of two per point inside
            # the loop below (round 24: this was 2 + N SELECTs).
            try:
                acc_by_kp = await self._accuracy_map(session, kp_ids)
            except Exception as e:
                logger.warning(f"Failed to get accuracy map: {e}")
                acc_by_kp = {}

            for node in all_nodes:
                has_q = node.id in question_coverage
                has_f = node.id in flashcard_coverage

                if not has_q and not has_f:
                    uncovered.append({
                        "id": node.id,
                        "title": node.title,
                        "level": node.level,
                    })

                # Check answer accuracy for this knowledge point
                acc = acc_by_kp.get(node.id)
                if acc is not None and acc < 0.7:
                    weak.append({
                        "id": node.id,
                        "title": node.title,
                        "accuracy": round(acc, 2),
                        "recommendation": "建议重新复习此知识点，多做相关练习题",
                    })

            return {
                "total_knowledge_points": total_kp,
                "covered_by_questions": covered_by_q,
                "covered_by_flashcards": covered_by_f,
                "full_coverage": full_coverage,
                "coverage_rate_questions": round(covered_by_q / total_kp, 4),
                "coverage_rate_flashcards": round(covered_by_f / total_kp, 4),
                "full_coverage_rate": round(full_coverage / total_kp, 4),
                "uncovered_points": uncovered,
                "weak_points": weak,
            }

    async def _accuracy_map(self, session, kp_ids: list[str]) -> dict[str, float]:
        """Accuracy per knowledge point, in ONE query.

        Two fixes over the previous per-point implementation:

        1. It used to run two queries *per knowledge point* from inside the loop
           in `calculate()` (measured: 2 points -> 5 SELECTs, 30 points -> 33).
           That is an N+1 and it scaled with the size of the summary.
        2. It summed `AnswerRecord.is_correct.cast(int)`. The Python built-in
           `int` is not a SQLAlchemy type, so building the expression raised
           "int object has no attribute _isnull" on every call. The exception
           was swallowed by the caller, so `weak_points` was **always empty**.
           tests/test_memory/test_coverage.py used to patch
           `sqlalchemy.sql.functions._type_from_args` to hide it; a portable
           CASE expression removes both the bug and the need for that patch.

        Answers are aggregated across all users, matching the previous
        behaviour (it never filtered by user either).
        """
        if not kp_ids:
            return {}

        stmt = (
            select(
                KnowledgeCoverage.knowledge_point_id,
                func.count().label("total"),
                func.sum(case((AnswerRecord.is_correct.is_(True), 1), else_=0)).label("correct"),
            )
            .select_from(KnowledgeCoverage)
            .join(AnswerRecord, AnswerRecord.question_id == KnowledgeCoverage.resource_id)
            .where(
                KnowledgeCoverage.knowledge_point_id.in_(kp_ids),
                KnowledgeCoverage.resource_type == "question",
            )
            .group_by(KnowledgeCoverage.knowledge_point_id)
        )
        rows = (await session.execute(stmt)).fetchall()

        result: dict[str, float] = {}
        for kp_id, total, correct in rows:
            if total and total > 0:
                result[kp_id] = (correct or 0) / total
        return result

    async def _get_accuracy(self, kp_id: str, session) -> float | None:
        """Accuracy rate for a single knowledge point (see `_accuracy_map`)."""
        try:
            return (await self._accuracy_map(session, [kp_id])).get(kp_id)
        except Exception as e:
            logger.warning(f"Failed to get accuracy for {kp_id}: {e}")
            return None

    async def ensure_full_coverage(
        self,
        summary_id: str,
        document_id: str,
        model: str = "deepseek-chat",
    ) -> dict:
        """Check coverage and auto-generate missing questions/cards.

        Returns:
            Dict with generated items count and updated coverage.
        """
        report = await self.calculate(summary_id)

        if not report["uncovered_points"]:
            return {"status": "already_covered", "generated": 0, "report": report}

        # Generate missing coverage
        from app.core.quiz import quiz_generator
        from app.core.memory import card_generator

        uncovered = report["uncovered_points"]
        generated_questions = 0
        generated_cards = 0

        async with async_session() as session:
            from app.models import KnowledgePointNode, KnowledgeCoverage

            # Batched (was one session.get() per uncovered point — a row-level N+1
            # whose query count grew with the size of the coverage gap).
            point_ids = [pt["id"] for pt in uncovered]
            nodes_by_id: dict[str, KnowledgePointNode] = {}
            if point_ids:
                nodes_result = await session.execute(
                    select(KnowledgePointNode).where(KnowledgePointNode.id.in_(point_ids))
                )
                nodes_by_id = {n.id: n for n in nodes_result.scalars().all()}

            for pt in uncovered:
                # Get knowledge point details
                node = nodes_by_id.get(pt["id"])
                if not node:
                    continue

                knowledge_text = f"{node.title}\n{node.explanation}"

                # Generate a question
                try:
                    questions = await quiz_generator.generate_questions(
                        chunk_texts=[knowledge_text],
                        question_type="single_choice",
                        count=1,
                        model=model,
                    )
                    for q in questions:
                        if isinstance(q, dict):
                            coverage = KnowledgeCoverage(
                                knowledge_point_id=pt["id"],
                                resource_type="question",
                                resource_id=q.get("id", ""),
                                is_primary=True,
                            )
                            session.add(coverage)
                            generated_questions += 1
                except Exception as e:
                    logger.error(f"Failed to generate question for {pt['id']}: {e}")

                # Generate a flashcard
                try:
                    cards = await card_generator.generate_cards(
                        knowledge_text=knowledge_text,
                        card_type="qa",
                        count=1,
                        deck_name="自动补全牌组",
                        model=model,
                    )
                    for c in cards:
                        if isinstance(c, dict):
                            coverage = KnowledgeCoverage(
                                knowledge_point_id=pt["id"],
                                resource_type="flashcard",
                                resource_id=c.get("id", ""),
                                is_primary=True,
                            )
                            session.add(coverage)
                            generated_cards += 1
                except Exception as e:
                    logger.error(f"Failed to generate flashcard for {pt['id']}: {e}")

            await session.commit()

        updated_report = await self.calculate(summary_id)
        return {
            "status": "filled",
            "generated_questions": generated_questions,
            "generated_cards": generated_cards,
            "report": updated_report,
        }


coverage_engine = CoverageEngine()
