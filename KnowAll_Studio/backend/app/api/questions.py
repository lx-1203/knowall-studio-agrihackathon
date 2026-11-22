"""Quiz generation, exam papers, and grading API routes (v2 with Bloom + Review)"""
from copy import deepcopy
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field, ValidationError
from app.database import get_db
from app.models import QuestionBank, ExamPaper, AnswerRecord, ExamSubmission, ErrorLog, QuizProgress, LiveQuestion, KnowledgeCoverage, Document, DocumentChunk, ReviewQueue
from app.core.quiz import quiz_generator, exam_engine, QuizGenerationConfig
from app.core.model_connections import choose_request_model
from app.core.auth import get_optional_user, get_current_user, get_user_id, load_user_api_keys
from app.core.documents import load_document_chunks
from app.core.knowledge.question_coverage import load_knowledge_points_by_question
from app.models import User
from app.core.learning_workbench import question_quality
from app.core.section_generation import generate_section_questions
from app.core.quiz.metadata import question_rubrics
from app.core.derived_document_access import require_derived_access, visible_derived_resources, visible_question_knowledge_points

router = APIRouter(prefix="/api/v1/quiz", tags=["quiz"])


def _rubrics(question: dict, status_code: int = 422) -> dict:
    try:
        return question_rubrics(question)
    except ValidationError as exc:
        raise HTTPException(status_code, '题目空位或评分点格式无效') from exc


class GenerateQuestionsRequest(BaseModel):
    document_id: str
    chunk_ids: list[str] | None = None
    question_type: str = "single_choice"
    count: int = Field(default=10, ge=1, le=100)
    difficulty: str = "medium"                    # legacy: easy/medium/hard
    difficulty_score: float = 0.5                 # NEW: continuous 0.0-1.0
    cognitive_level: str = "L2_understand"        # NEW: Bloom level
    enable_review: bool = True                    # NEW: LLM-as-Judge review
    preview: bool = True                          # NEW: if True, return questions without auto-saving
    model: str = "deepseek-chat"


class SaveToBankRequest(BaseModel):
    """Batch save selected questions to the question bank."""
    questions: list[dict]
    source_chunk_id: str | None = None


class CreateExamRequest(BaseModel):
    title: str = "试卷"
    question_ids: list[str] | None = None
    config: dict = {}


class SubmitExamRequest(BaseModel):
    paper_id: str
    answers: dict[str, str]
    enable_semantic: bool = True  # NEW: use LLM semantic grading for open-ended questions
    model: str = "deepseek-chat"  # model for semantic grading


@router.post("/generate")
async def generate_questions(
    req: GenerateQuestionsRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_optional_user),
):
    """Generate quiz questions from document chunks (v2 with Bloom + Review)."""
    await load_user_api_keys(get_user_id(current_user), db, current_user is not None)

    # Get chunks
    chunks = await load_document_chunks(db, req.document_id, req.chunk_ids, user_id=getattr(current_user, 'id', None))
    if not chunks:
        raise HTTPException(404, "No chunks found")

    if current_user is not None:
        await choose_request_model(req, get_user_id(current_user), db)

    config = QuizGenerationConfig(
        question_type=req.question_type,
        count=req.count,
        difficulty=req.difficulty,
        difficulty_score=req.difficulty_score,
        cognitive_level=req.cognitive_level,
        enable_review=req.enable_review,
    )

    try:
        questions, coverage, generation_errors = await generate_section_questions(chunks, config, req.model, quiz_generator)
        if not questions:
            raise HTTPException(502, '所有有预算的资料分段均未生成题目，请重试')
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Generation failed: {str(e)}")

    # Build response question dicts (with temporary IDs for preview)
    preview_questions = []
    for i, q in enumerate(questions):
        preview_questions.append({
            **_rubrics(q, 502),
            "_idx": i,
            "_selected": True,  # default checked
            "question_type": q.get("type", q.get("question_type", req.question_type)),
            "difficulty": q.get("difficulty", req.difficulty),
            "difficulty_score": q.get("difficulty_score", req.difficulty_score),
            "cognitive_level": q.get("cognitive_level", req.cognitive_level),
            "tags": q.get("tags", []),
            "question_text": q.get("question_text", ""),
            "options": q.get("options", []),
            "answer": str(q.get("answer", q.get("correct_answer", ""))),
            "analysis": q.get("analysis", ""),
            "review_scores": q.get("review_scores", {}),
            "review_total": q.get("review_total"),
            "reviewed": q.get("reviewed", False),
            "review_decision": q.get("review_decision", ""),
            "source_chunk_id": q.get("source_chunk_id"),
            "source_section": q.get("source_section"),
            "source_hash": q.get("source_hash"),
            "quality": question_quality(q),
        })

    if req.preview:
        # Preview mode: return questions without saving
        return {
            "mode": "preview",
            "generated_count": len(preview_questions),
            "reviewed_count": sum(1 for q in questions if q.get("reviewed")),
            "questions": preview_questions,
            "coverage": coverage,
            "generation_errors": generation_errors,
        }

    # Auto-save mode (backward compatible)
    saved_questions = []
    for q in questions:
        db_q = QuestionBank(
            question_type=q.get("type", q.get("question_type", req.question_type)),
            difficulty=q.get("difficulty", req.difficulty),
            difficulty_score=q.get("difficulty_score", req.difficulty_score),
            cognitive_level=q.get("cognitive_level", req.cognitive_level),
            tags=q.get("tags", []),
            question_text=q.get("question_text", ""),
            options=q.get("options", []),
            correct_answer=str(q.get("answer", q.get("correct_answer", ""))),
            **_rubrics(q, 502),
            analysis=q.get("analysis", ""),
            review_scores=q.get("review_scores", {}),
            review_total=q.get("review_total"),
            source_chunk_id=q.get("source_chunk_id"),
        )
        db.add(db_q)
        saved_questions.append(db_q)

    await db.commit()

    return {
        "mode": "saved",
        "coverage": coverage,
        "generation_errors": generation_errors,
        "generated_count": len(saved_questions),
        "reviewed_count": sum(1 for q in questions if q.get("reviewed")),
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
                "answer": q.correct_answer,
                "blanks": q.blanks or [],
                "scoring_points": q.scoring_points or [],
                "analysis": q.analysis,
                "review_scores": q.review_scores,
                "review_total": q.review_total,
            }
            for q in saved_questions
        ],
    }


@router.post("/exam/create")
async def create_exam(req: CreateExamRequest, db: AsyncSession = Depends(get_db), current_user=Depends(get_optional_user)):
    """Create an exam paper from existing questions or auto-generated."""
    from sqlalchemy import select

    if req.question_ids:
        result = await db.execute(
            select(QuestionBank).where(QuestionBank.id.in_(req.question_ids))
        )
        questions = result.scalars().all()
        if len(await visible_derived_resources(db, questions, getattr(current_user, 'id', None))) != len(questions):
            raise HTTPException(404, "Question not found")
        question_dicts = [
            {
                "id": q.id,
                "question_type": q.question_type,
                "difficulty": q.difficulty,
                "difficulty_score": q.difficulty_score,
                "cognitive_level": q.cognitive_level,
                "tags": q.tags,
                "question_text": q.question_text,
                "options": q.options,
                "answer": q.correct_answer,
                "blanks": q.blanks or [],
                "scoring_points": q.scoring_points or [],
                "analysis": q.analysis,
            }
            for q in questions
        ]
        excluded_ids = [q['id'] for q in question_dicts if not question_quality(q)['eligible']]
        question_dicts = [q for q in question_dicts if question_quality(q)['eligible']]
        paper_data = {"title": req.title, "questions": question_dicts, "question_ids": [q["id"] for q in question_dicts]}
    else:
        result = await db.execute(select(QuestionBank))
        all_questions = (await visible_derived_resources(db, result.scalars().all(), getattr(current_user, 'id', None)))[:200]
        question_dicts = [
            {
                "id": q.id,
                "question_type": q.question_type,
                "difficulty": q.difficulty,
                "difficulty_score": q.difficulty_score,
                "cognitive_level": q.cognitive_level,
                "tags": q.tags,
                "question_text": q.question_text,
                "options": q.options,
                "answer": q.correct_answer,
                "blanks": q.blanks or [],
                "scoring_points": q.scoring_points or [],
                "analysis": q.analysis,
            }
            for q in all_questions
        ]
        excluded_ids = [q['id'] for q in question_dicts if not question_quality(q)['eligible']]
        question_dicts = [q for q in question_dicts if question_quality(q)['eligible']]
        paper_data = exam_engine.create_paper(question_dicts, req.config | {"title": req.title})

    if not paper_data['question_ids']:
        raise HTTPException(400, '没有可练习的题目，请先修复题目质量问题')

    paper = ExamPaper(
        title=paper_data["title"],
        question_ids=paper_data["question_ids"],
        config=req.config,
    )
    db.add(paper)
    await db.commit()
    await db.refresh(paper)

    return {
        "paper_id": paper.id,
        "title": paper.title,
        "question_count": len(paper_data["question_ids"]),
        "total_score": len(paper_data["question_ids"]) * 5,
        "questions": paper_data.get("questions", question_dicts),
        "excluded_question_ids": excluded_ids,
    }


@router.post("/exam/submit")
async def submit_exam(
    req: SubmitExamRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_optional_user),
):
    """Submit exam answers, get graded results with cognitive analysis."""
    from sqlalchemy import select

    user_id = get_user_id(current_user)

    # Check for duplicate submission
    dup_result = await db.execute(
        select(AnswerRecord.id).where(
            AnswerRecord.user_id == user_id,
            AnswerRecord.paper_id == req.paper_id,
        ).union_all(select(ExamSubmission.paper_id).where(
            ExamSubmission.user_id == user_id,
            ExamSubmission.paper_id == req.paper_id,
        )).limit(1)
    )
    if dup_result.scalar_one_or_none():
        raise HTTPException(400, "你已经提交过该试卷，不能重复提交")

    result = await db.execute(select(ExamPaper).where(ExamPaper.id == req.paper_id))
    paper = result.scalar_one_or_none()
    if not paper:
        raise HTTPException(404, "Paper not found")

    paper_ids = paper.question_ids
    if not isinstance(paper_ids, list) or not paper_ids or any(not isinstance(qid, str) for qid in paper_ids):
        raise HTTPException(409, "试卷题目已失效，请重新组卷")
    result = await db.execute(
        select(QuestionBank).where(QuestionBank.id.in_(paper_ids))
    )
    questions = result.scalars().all()

    if len(await visible_derived_resources(db, questions, getattr(current_user, 'id', None))) != len(questions):
        raise HTTPException(404, "Question not found")

    if {q.id for q in questions} != set(paper_ids):
        raise HTTPException(409, "试卷题目已失效，请重新组卷")
    def grading_view(q):
        return {
            "id": q.id,
            "question_type": q.question_type,
            "question_text": q.question_text,
            "options": q.options,
            "answer": q.correct_answer,
            "blanks": q.blanks or [],
            "scoring_points": q.scoring_points or [],
            "analysis": q.analysis,
            "cognitive_level": q.cognitive_level,
            "difficulty_score": q.difficulty_score,
        }

    question_dicts = [grading_view(q) for q in questions]
    grading_snapshot = deepcopy({q["id"]: q for q in question_dicts})

    paper_with_qs = {"questions": question_dicts}
    results = await exam_engine.grade_enhanced(
        paper_with_qs, req.answers,
        enable_semantic=req.enable_semantic,
        model=req.model,
    )

    # Grading may await a model. Re-read persisted sources before writing any
    # claim/history, rather than trusting pre-await ORM identities/results.
    current_ids = await db.scalar(select(ExamPaper.question_ids).where(ExamPaper.id == req.paper_id))
    if current_ids != paper_ids:
        raise HTTPException(409, "试卷题目已变更，请重新组卷后提交")
    current_questions = (await db.scalars(
        select(QuestionBank).where(QuestionBank.id.in_(paper_ids)).execution_options(populate_existing=True)
    )).all()
    if len(await visible_derived_resources(db, current_questions, getattr(current_user, 'id', None))) != len(current_questions):
        raise HTTPException(404, "Question not found")
    if {q.id: grading_view(q) for q in current_questions} != grading_snapshot:
        raise HTTPException(409, "试卷题目已变更，请重新组卷后提交")

    # Claim only after grading; keep the claim and every answer/error update in
    # one transaction, so cancellation or failed commit leaves a retry possible.
    db.add(ExamSubmission(user_id=user_id, paper_id=req.paper_id))
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        committed = await db.get(ExamSubmission, (user_id, req.paper_id))
        if committed is not None:
            raise HTTPException(400, "你已经提交过该试卷，不能重复提交")
        raise

    # Save answer records.
    # The ErrorLog lookup below used to run once per wrong answer (a 50-question
    # paper with 30 wrong = 30 extra SELECTs on a hot path); it is now a single
    # batched IN query. Guarded by tests/test_quiz/test_exam_submit_queries.py.
    wrong_qids = [d["question_id"] for d in results["details"] if not d["is_correct"]]
    error_by_qid: dict[str, ErrorLog] = {}
    if wrong_qids:
        existing = await db.execute(
            select(ErrorLog).where(
                ErrorLog.question_id.in_(wrong_qids),
                ErrorLog.user_id == user_id,
            )
        )
        error_by_qid = {e.question_id: e for e in existing.scalars().all()}

    error_ts = datetime.now(timezone.utc).replace(tzinfo=None)
    for detail in results["details"]:
        record = AnswerRecord(
            user_id=user_id,
            question_id=detail["question_id"],
            paper_id=req.paper_id,
            user_answer=detail["user_answer"],
            is_correct=detail["is_correct"],
        )
        db.add(record)

        if not detail["is_correct"]:
            error = error_by_qid.get(detail["question_id"])
            if error:
                error.error_count += 1
                error.last_error_at = error_ts
            else:
                db.add(ErrorLog(
                    user_id=user_id,
                    question_id=detail["question_id"],
                ))

    await db.commit()

    # Add cognitive breakdown to results.
    # Index questions by id instead of scanning the whole list per detail — the
    # old `next(...)` inside this loop was O(details x questions).
    question_by_id = {q["id"]: q for q in question_dicts}
    cognitive_breakdown = {}
    for detail in results["details"]:
        q_dict = question_by_id.get(detail["question_id"])
        cl = q_dict.get("cognitive_level", "unknown") if q_dict else "unknown"
        if cl not in cognitive_breakdown:
            cognitive_breakdown[cl] = {"total": 0, "correct": 0}
        cognitive_breakdown[cl]["total"] += 1
        if detail["is_correct"]:
            cognitive_breakdown[cl]["correct"] += 1

    results["cognitive_breakdown"] = {
        cl: {
            "total": stats["total"],
            "correct": stats["correct"],
            "accuracy": round(stats["correct"] / max(1, stats["total"]) * 100, 1),
        }
        for cl, stats in cognitive_breakdown.items()
    }

    return results


@router.get("/errors")
async def get_error_questions(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_optional_user),
):
    """Get all error questions for the current user."""
    from sqlalchemy import select

    user_id = get_user_id(current_user)
    result = await db.execute(
        select(ErrorLog, QuestionBank)
        .join(QuestionBank, ErrorLog.question_id == QuestionBank.id)
        .where(ErrorLog.user_id == user_id)
        .order_by(ErrorLog.last_error_at.desc())
    )
    rows = result.all()
    visible_ids = {q.id for q in await visible_derived_resources(db, [q for _, q in rows], getattr(current_user, 'id', None))}
    rows = [(err, q) for err, q in rows if q.id in visible_ids]
    return [
        {
            "error_id": err.id,
            "error_count": err.error_count,
            "last_error_at": err.last_error_at.isoformat() if err.last_error_at else None,
            "question": {
                "id": q.id,
                "question_type": q.question_type,
                "difficulty": q.difficulty,
                "difficulty_score": q.difficulty_score,
                "cognitive_level": q.cognitive_level,
                "question_text": q.question_text,
                "options": q.options,
                "answer": q.correct_answer,
                "blanks": q.blanks or [],
                "scoring_points": q.scoring_points or [],
                "analysis": q.analysis,
            },
        }
        for err, q in rows
    ]


@router.post("/errors/{error_id}/variants")
async def generate_error_variants(
    error_id: str, count: int = Query(default=3, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_optional_user),
):
    """Generate variant questions for a specific error question."""
    from sqlalchemy import select

    result = await db.execute(
        select(ErrorLog, QuestionBank)
        .join(QuestionBank, ErrorLog.question_id == QuestionBank.id)
        .where(ErrorLog.id == error_id, ErrorLog.user_id == get_user_id(current_user))
    )
    row = result.first()
    if not row:
        raise HTTPException(404, "Error entry not found")

    error, question = row
    await require_derived_access(db, question, getattr(current_user, 'id', None))
    await load_user_api_keys(get_user_id(current_user), db, current_user is not None)

    question_dict = {
        "question_type": question.question_type,
        "question_text": question.question_text,
        "correct_answer": question.correct_answer,
        "analysis": question.analysis,
        "difficulty": question.difficulty,
        "difficulty_score": question.difficulty_score,
        "cognitive_level": question.cognitive_level,
        "options": question.options or [],
        **_rubrics({"blanks": question.blanks, "scoring_points": question.scoring_points}, status_code=409),
    }

    try:
        variants = await quiz_generator.generate_variants(question_dict, count)
    except Exception as e:
        raise HTTPException(502, f"Generation failed: {str(e)}")

    normalized = quiz_generator._normalize_questions(variants, question.question_type)
    if not normalized or len(normalized) != len(variants) or len(normalized) > count:
        raise HTTPException(502, "变式题格式无效，请重新生成")

    saved = []
    for v in normalized:
        db_q = QuestionBank(
            question_type=question.question_type,
            difficulty=question.difficulty,
            difficulty_score=question.difficulty_score,
            cognitive_level=question.cognitive_level,
            tags=question.tags,
            question_text=v.get("question_text", ""),
            options=v.get("options", []),
            correct_answer=str(v.get("answer", v.get("correct_answer", ""))),
            analysis=v.get("analysis", ""),
            **_rubrics(v, status_code=502),
            parent_question_id=question.id,
        )
        db.add(db_q)
        saved.append(db_q)

    error.variant_generated = True
    await db.commit()

    return {
        "variant_count": len(saved),
        "questions": [
            {
                "id": q.id,
                "question_type": q.question_type,
                "difficulty_score": q.difficulty_score,
                "cognitive_level": q.cognitive_level,
                "question_text": q.question_text,
                "options": q.options,
                "answer": q.correct_answer,
                "blanks": q.blanks or [],
                "scoring_points": q.scoring_points or [],
            }
            for q in saved
        ],
    }


# ===== Question Bank Management =====

async def _delete_bank_questions(db: AsyncSession, ids: list[str], *, single: bool = False, user_id: str | None = None) -> int:
    ids = list(dict.fromkeys(ids))
    if not ids:
        return 0
    rows = list((await db.scalars(select(QuestionBank).where(QuestionBank.id.in_(ids)))).all())
    if len(await visible_derived_resources(db, rows, user_id)) != len(rows):
        raise HTTPException(404, "Question not found")
    existing = [q.id for q in rows]
    if single and not existing:
        raise HTTPException(404, "Question not found")
    if not existing:
        return 0
    conflict = "题目已有关联答题、错题、课堂、变式、复习或试卷记录，暂不能删除"
    # Keep the existing FK protection even on SQLite installations where FK
    # enforcement is off. Deleting learning history is not a bank operation.
    for model in (AnswerRecord, ErrorLog, LiveQuestion):
        if await db.scalar(select(model.id).where(model.question_id.in_(existing)).limit(1)):
            raise HTTPException(409, conflict)
    # Soft references have no FK enforcement. A surviving variant still needs
    # its parent for inherited source access; deleting the whole family is OK.
    if await db.scalar(select(QuestionBank.id).where(
        QuestionBank.parent_question_id.in_(existing),
        QuestionBank.id.not_in(existing),
    ).limit(1)):
        raise HTTPException(409, conflict)
    if await db.scalar(select(ReviewQueue.id).where(
        ReviewQueue.resource_type == "question",
        ReviewQueue.resource_id.in_(existing),
    ).limit(1)):
        raise HTTPException(409, conflict)
    # JSON membership must be exact, including on SQLite. Stream instead of
    # loading all papers, and release the cursor on conflict/failure/cancel.
    existing_ids = set(existing)
    papers = await db.stream_scalars(select(ExamPaper.question_ids).execution_options(yield_per=200))
    try:
        async for paper_ids in papers:
            if isinstance(paper_ids, list) and any(isinstance(qid, str) and qid in existing_ids for qid in paper_ids):
                raise HTTPException(409, conflict)
    finally:
        await papers.close()
    try:
        await db.execute(delete(KnowledgeCoverage).where(
            KnowledgeCoverage.resource_type == "question",
            KnowledgeCoverage.resource_id.in_(existing),
        ))
        result = await db.execute(delete(QuestionBank).where(QuestionBank.id.in_(existing)))
        await db.commit()
    except IntegrityError:
        # A reference can be committed after preflight. With FK enforcement,
        # roll back the whole batch, including its coverage cleanup.
        await db.rollback()
        raise HTTPException(409, conflict) from None
    except BaseException:
        await db.rollback()
        raise
    return result.rowcount

@router.post("/bank/save")
async def save_to_bank(
    req: SaveToBankRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """Batch save selected preview questions to the question bank."""
    if not req.questions:
        raise HTTPException(400, "No questions provided")

    saved = []
    for q in req.questions:
        review_scores = dict(q.get("review_scores") or {})
        review_scores.pop('knowledge_owner_id', None)
        db_q = QuestionBank(
            question_type=q.get("question_type", q.get("type", "single_choice")),
            difficulty=q.get("difficulty", "medium"),
            difficulty_score=q.get("difficulty_score", 0.5),
            cognitive_level=q.get("cognitive_level", "L2_understand"),
            tags=q.get("tags", []),
            question_text=q.get("question_text", ""),
            options=q.get("options", []),
            correct_answer=str(q.get("answer", q.get("correct_answer", ""))),
            **_rubrics(q, 422),
            analysis=q.get("analysis", ""),
            review_scores=review_scores,
            review_total=q.get("review_total"),
            source_chunk_id=q.get('source_chunk_id') or req.source_chunk_id,
        )
        db.add(db_q)
        saved.append(db_q)

    await db.flush()
    if len(await visible_derived_resources(db, saved, getattr(current_user, 'id', None))) != len(saved):
        raise HTTPException(404, "Source not found")
    chunk_ids = {q.source_chunk_id for q in saved if q.source_chunk_id}
    ownership = dict((await db.execute(select(DocumentChunk.id, Document.metadata_).join(Document, Document.id == DocumentChunk.doc_id).where(DocumentChunk.id.in_(chunk_ids)))).all()) if chunk_ids else {}
    for q in saved:
        owner = (ownership.get(q.source_chunk_id) or {}).get('knowledge_owner_id')
        if owner:
            q.review_scores = {**q.review_scores, 'knowledge_owner_id': owner}
    await db.commit()

    return {
        "saved_count": len(saved),
        "question_ids": [q.id for q in saved],
        "questions": [
            {
                "id": q.id,
                "question_type": q.question_type,
                "difficulty_score": q.difficulty_score,
                "cognitive_level": q.cognitive_level,
                "question_text": q.question_text[:80] + "..." if len(q.question_text or "") > 80 else q.question_text,
            }
            for q in saved
        ],
    }


@router.delete("/bank/{question_id}")
async def delete_question(
    question_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """Delete a single question from the bank."""
    await _delete_bank_questions(db, [question_id], single=True, user_id=getattr(current_user, 'id', None))
    return {"deleted": question_id}


@router.post("/bank/delete-batch")
async def delete_questions_batch(
    ids: list[str],
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """Delete multiple questions from the bank."""
    return {"deleted_count": await _delete_bank_questions(db, ids, user_id=getattr(current_user, 'id', None))}


@router.get("/questions")
async def list_questions(
    question_type: str | None = None,
    difficulty: str | None = None,
    cognitive_level: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """List questions with optional filters (v2 with cognitive_level)."""
    from sqlalchemy import select

    query = select(QuestionBank)
    if question_type:
        query = query.where(QuestionBank.question_type == question_type)
    if difficulty:
        query = query.where(QuestionBank.difficulty == difficulty)
    if cognitive_level:
        query = query.where(QuestionBank.cognitive_level == cognitive_level)
    result = await db.execute(query)
    questions = (await visible_derived_resources(db, result.scalars().all(), getattr(current_user, 'id', None)))[offset:offset + limit]

    # Attach knowledge points (KnowledgeCoverage → KnowledgePointNode) so the
    # frontend can do provenance/linking/grouping. No answers are included here.
    # Single batched query, shared with interactive_quiz (see app/core/knowledge/question_coverage.py).
    q_ids = [q.id for q in questions]
    kp_by_qid = await load_knowledge_points_by_question(db, q_ids)
    kp_by_qid = await visible_question_knowledge_points(db, kp_by_qid, getattr(current_user, 'id', None))

    return [
        {
            "id": q.id,
            "question_type": q.question_type,
            "difficulty": q.difficulty,
            "difficulty_score": q.difficulty_score,
            "cognitive_level": q.cognitive_level,
            "tags": q.tags,
            "question_text": q.question_text[:100] + "..." if len(q.question_text or "") > 100 else q.question_text,
            "knowledge_points": kp_by_qid.get(q.id, []),
        }
        for q in questions
    ]


# ===== 刷题进度持久化（浏览器崩溃/换设备后可续刷） =====
# 强制鉴权：题库页未登录时后端直接拒绝读写进度（401），配合前端登录门。
# data 的 JSON 内额外记 agreed（是否已同意用户协议）以便跨设备同步。

class QuizProgressData(BaseModel):
    """前端刷题状态：answers 为 {题目索引: 选项索引}，idx 为当前题号"""
    answers: dict[str, int] = {}
    wrongList: bool = False
    idx: int = 0
    filter: str = "all"
    agreed: bool = False


@router.get("/progress")
async def get_quiz_progress(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """读取当前用户的刷题进度（需登录）。"""
    user_id = current_user.id
    result = await db.execute(select(QuizProgress).where(QuizProgress.user_id == user_id))
    row = result.scalar_one_or_none()
    if not row:
        return {"user_id": user_id, "data": {}}
    return {"user_id": user_id, "data": row.data}


@router.put("/progress")
async def save_quiz_progress(
    req: QuizProgressData,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """保存当前用户的刷题进度（覆盖式，需登录）。"""
    user_id = current_user.id
    result = await db.execute(select(QuizProgress).where(QuizProgress.user_id == user_id))
    row = result.scalar_one_or_none()
    data = req.model_dump()
    data["updated_at"] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    if row:
        row.data = data
    else:
        db.add(QuizProgress(user_id=user_id, data=data))
    await db.commit()
    return {"ok": True, "user_id": user_id}
