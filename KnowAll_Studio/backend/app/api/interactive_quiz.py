"""Interactive quiz API routes"""
import uuid
import asyncio
from copy import deepcopy
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field, ValidationError
from app.database import get_db
from app.models import QuestionBank, AnswerRecord, KnowledgeCoverage
from app.core.auth import get_optional_user, get_user_id, load_user_api_keys
from app.core.knowledge.question_coverage import load_knowledge_points_by_question
from app.core.quiz import exam_engine
from app.core.quiz.metadata import question_rubrics
from app.core.learning_workbench import question_quality
from app.core.derived_document_access import require_derived_access, visible_derived_resources, visible_question_knowledge_points

router = APIRouter(prefix="/api/v1/quiz/interactive", tags=["interactive_quiz"])

# In-memory session store (in production, use Redis)
_sessions: dict[str, dict] = {}


def _owned_session(session_id: str, user_id: str) -> dict:
    session = _sessions.get(session_id)
    if session is None or session.get("user_id") != user_id:
        raise HTTPException(404, "Session not found or expired")
    return session


class StartQuizRequest(BaseModel):
    summary_id: str
    count: int = 10
    mode: str = "sequential"  # sequential / random
    question_types: list[str] | None = None


class SubmitAnswerRequest(BaseModel):
    question_id: str
    user_answer: str
    time_spent_ms: int = Field(default=0, ge=0)
    knowledge_point_id: str | None = None
    session_id: str | None = None


@router.get("/start")
async def start_interactive_quiz(
    summary_id: str,
    count: int = Query(default=10, ge=1, le=100),
    mode: str = "sequential",
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """Start an interactive quiz session. Returns first batch of questions."""
    from app.models import KnowledgeSummary
    summary = await db.get(KnowledgeSummary, summary_id)
    if summary is not None:
        await require_derived_access(db, summary, getattr(current_user, 'id', None))
    await load_user_api_keys(get_user_id(current_user), db, current_user is not None)

    # Find questions linked to this summary's knowledge points
    from app.models import KnowledgePointNode

    # Get coverage entries for this summary
    kp_stmt = select(KnowledgePointNode.id).where(KnowledgePointNode.summary_id == summary_id)
    kp_result = await db.execute(kp_stmt)
    kp_ids = [row[0] for row in kp_result.fetchall()]

    if not kp_ids:
        # Fall back to loading all questions
        q_stmt = select(QuestionBank)
    else:
        cov_stmt = select(KnowledgeCoverage.resource_id).where(
            KnowledgeCoverage.knowledge_point_id.in_(kp_ids),
            KnowledgeCoverage.resource_type == "question",
        )
        cov_result = await db.execute(cov_stmt)
        q_ids = [row[0] for row in cov_result.fetchall()]

        if not q_ids:
            return {"session_id": "", "questions": [], "total": 0, "message": "No questions found for this summary. Run Agent orchestration first."}

        q_stmt = select(QuestionBank).where(QuestionBank.id.in_(q_ids))

    q_result = await db.execute(q_stmt)
    visible = await visible_derived_resources(db, q_result.scalars().all(), getattr(current_user, 'id', None))
    questions = [q for q in visible if question_quality({
        'question_text': q.question_text, 'question_type': q.question_type,
        'options': q.options, 'answer': q.correct_answer,
        'blanks': q.blanks, 'scoring_points': q.scoring_points,
    })['eligible']][:count]

    # Attach knowledge points (KnowledgeCoverage → KnowledgePointNode) so the
    # frontend can do provenance/linking/grouping. Single batched query, shared
    # with the quiz bank listing (see app/core/knowledge/question_coverage.py).
    q_ids = [q.id for q in questions]
    kp_by_qid = await load_knowledge_points_by_question(db, q_ids)
    kp_by_qid = await visible_question_knowledge_points(db, kp_by_qid, getattr(current_user, 'id', None))

    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "user_id": get_user_id(current_user),
        "submit_lock": asyncio.Lock(),
        "questions": [
            {
                "id": q.id,
                "question_type": q.question_type,
                "difficulty": q.difficulty,
                "difficulty_score": q.difficulty_score,
                "cognitive_level": q.cognitive_level,
                "tags": q.tags,
                "question_text": q.question_text,
                "options": q.options,
                "correct_answer": q.correct_answer,
                "analysis": q.analysis,
                "knowledge_points": kp_by_qid.get(q.id, []),
                **deepcopy(question_rubrics({'blanks': q.blanks, 'scoring_points': q.scoring_points})),
            }
            for q in questions
        ],
        "answers": {},
        "current_index": 0,
    }

    # Return questions WITHOUT answer/analysis (these are graded server-side on
    # submit). Sending them here let the client read the answer before answering.
    client_questions = [
        {k: v for k, v in q.items() if k not in ("correct_answer", "analysis", "blanks", "scoring_points")}
        for q in _sessions[session_id]["questions"]
    ]

    return {
        "session_id": session_id,
        "questions": client_questions,
        "total": len(questions),
    }


@router.post("/submit")
async def submit_interactive_answer(
    req: SubmitAnswerRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """Submit an answer and get immediate feedback."""
    user_id = get_user_id(current_user)
    if req.session_id is None:
        # Keep the existing stand-alone practice contract for older callers.
        return await _grade_and_save_answer(req, db, user_id, None)

    session = _owned_session(req.session_id, user_id)
    question = next((q for q in session["questions"] if q["id"] == req.question_id), None)
    if question is None:
        raise HTTPException(400, "Question does not belong to this session")
    if req.knowledge_point_id is not None and req.knowledge_point_id not in {
        kp["id"] for kp in question.get("knowledge_points", [])
    }:
        raise HTTPException(400, "Knowledge point does not belong to this question")

    # One worker owns this in-memory session. Keep the duplicate check, SQL
    # commit and stats update together; failure/cancellation releases the lock
    # without marking the question answered, so a subsequent retry can proceed.
    async with session["submit_lock"]:
        if req.question_id in session["answers"]:
            raise HTTPException(409, "Question already answered in this session")
        return await _grade_and_save_answer(req, db, user_id, session)


async def _grade_and_save_answer(
    req: SubmitAnswerRequest, db: AsyncSession, user_id: str, session: dict | None,
):

    # Look up the question
    question = await db.get(QuestionBank, req.question_id)
    if not question:
        raise HTTPException(404, "Question not found")
    await require_derived_access(db, question, user_id if user_id != 'local_user' else None)
    if req.knowledge_point_id:
        from app.models import KnowledgePointNode
        node = await db.get(KnowledgePointNode, req.knowledge_point_id)
        if node is not None:
            await require_derived_access(db, node, user_id if user_id != 'local_user' else None)

    # Keep the SQL existence check: deleted bank questions cannot acquire new
    # answer records. A live session is graded against the version it displayed,
    # while stand-alone practice continues to use the current bank question.
    if session is not None:
        snapshot = next(q for q in session["questions"] if q["id"] == req.question_id)
        grading_question = {**snapshot, "answer": snapshot["correct_answer"]}
    else:
        grading_question = {
            "id": question.id,
            "question_type": question.question_type,
            "answer": question.correct_answer,
            "analysis": question.analysis,
            "question_text": question.question_text,
            "options": question.options,
        }
        try:
            grading_question.update(question_rubrics({'blanks': question.blanks, 'scoring_points': question.scoring_points}))
        except ValidationError:
            raise HTTPException(409, "Question scoring rules are invalid; repair the bank question first")

    result = exam_engine.grade(
        paper={"questions": [grading_question]},
        user_answers={question.id: req.user_answer},
        time_spent_ms={question.id: req.time_spent_ms},
        knowledge_point_ids={question.id: req.knowledge_point_id or ""},
    )

    detail = result["details"][0] if result["details"] else {}
    rubric_feedback = {key: detail[key] for key in (
        "partial_score", "blank_details", "point_details", "earned_score", "score_total",
    ) if key in detail}

    # Save answer record
    record = AnswerRecord(
        user_id=user_id,
        question_id=req.question_id,
        user_answer=req.user_answer,
        is_correct=detail.get("is_correct", False),
        time_spent_ms=req.time_spent_ms,
        attempt_count=1,
        knowledge_point_ids=[req.knowledge_point_id] if req.knowledge_point_id else [],
    )
    db.add(record)
    await db.commit()

    # Write the graded answer back into the session so that /session/{id}/stats
    # reflects real data (previously stats always returned 0 because answers
    # were never stored in the session).
    session_stats = {}
    if session is not None:
        session["answers"][req.question_id] = {
            "question_id": req.question_id,
            "is_correct": detail.get("is_correct", False),
            "user_answer": req.user_answer,
            "correct_answer": detail.get("correct_answer", ""),
            "analysis": detail.get("analysis", ""),
            "time_spent_ms": req.time_spent_ms,
            **rubric_feedback,
        }
        answers = session["answers"]
        total_answered = len(answers)
        correct = sum(1 for a in answers.values() if a.get("is_correct"))
        session_stats = {
            "total_answered": total_answered,
            "correct": correct,
            "accuracy": round(correct / max(1, total_answered), 4),
            "questions_detail": list(answers.values()),
        }

    return {
        "is_correct": detail.get("is_correct", False),
        "correct_answer": detail.get("correct_answer", ""),
        "analysis": detail.get("analysis", ""),
        "stats": session_stats,
        **rubric_feedback,
    }


@router.get("/session/{session_id}/stats")
async def get_session_stats(session_id: str, current_user=Depends(get_optional_user)):
    """Get stats for an interactive quiz session."""
    session = _owned_session(session_id, get_user_id(current_user))

    answers = session.get("answers", {})
    total = len(answers)
    correct = sum(1 for a in answers.values() if a.get("is_correct"))

    return {
        "total_answered": total,
        "correct": correct,
        "accuracy": round(correct / max(1, total), 4),
        "time_spent_total_ms": sum(a.get("time_spent_ms", 0) for a in answers.values()),
        "questions_detail": list(answers.values()),
    }
