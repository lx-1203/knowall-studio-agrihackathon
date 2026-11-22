"""Knowledge-point lookup for question resources.

Why this module exists
----------------------
`pylint --enable=duplicate-code` (R0801) found a 26-line byte-identical block in
two routers — identical down to the SQLAlchemy column list and the `order_by`,
differing only in the name of the local `Result` variable:

  * `app/api/questions.py::list_questions`
  * `app/api/interactive_quiz.py::start_interactive_quiz`

Both attach knowledge points to a list of questions so the UI can show which
knowledge each question covers. That is a single query concern, so it now lives
in a single place.

Relationship to other modules
-----------------------------
`app/core/knowledge/` already hosts `summary_generator` and `bois_analyzer` as
sibling modules; this is the coverage-read side of the same domain.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeCoverage, KnowledgePointNode


async def load_knowledge_points_by_question(
    db: AsyncSession,
    question_ids: list[str],
) -> dict[str, list[dict]]:
    """Map each question id to its knowledge points.

    Knowledge points are joined through `KnowledgeCoverage` rows whose
    ``resource_type == "question"``, ordered by ``(level, sequence)`` so the
    returned lists are in knowledge-tree order.

    Args:
        db: Active async session.
        question_ids: Question ids to resolve. May be empty.

    Returns:
        ``{question_id: [kp_dict, ...]}``. Questions with no coverage are
        **absent** from the mapping rather than present with an empty list —
        callers use ``.get(qid, [])``. Returns ``{}`` for empty input (the
        original blocks short-circuited the same way, and an ``IN ()`` query
        would be wasted round-trip).
    """
    kp_by_qid: dict[str, list[dict]] = {}
    if not question_ids:
        return kp_by_qid

    stmt = (
        select(
            KnowledgeCoverage.resource_id,
            KnowledgePointNode.id,
            KnowledgePointNode.title,
            KnowledgePointNode.explanation,
            KnowledgePointNode.related_concepts,
            KnowledgePointNode.parent_id,
            KnowledgePointNode.level,
        )
        .join(
            KnowledgePointNode,
            KnowledgeCoverage.knowledge_point_id == KnowledgePointNode.id,
        )
        .where(
            KnowledgeCoverage.resource_type == "question",
            KnowledgeCoverage.resource_id.in_(question_ids),
        )
        .order_by(KnowledgePointNode.level, KnowledgePointNode.sequence)
    )

    for row in (await db.execute(stmt)).all():
        kp_by_qid.setdefault(row.resource_id, []).append({
            "id": row.id,
            "title": row.title,
            "explanation": row.explanation,
            "related_concepts": row.related_concepts,
            "parent_id": row.parent_id,
            "level": row.level,
        })
    return kp_by_qid
