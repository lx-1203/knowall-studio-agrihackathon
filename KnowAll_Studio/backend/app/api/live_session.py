"""实时对话考试 API 路由"""
import random
import string
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    LiveSession, LiveParticipant, LiveQuestion, LiveAnswer,
    QuestionBank, gen_uuid,
)
from app.core.auth import get_optional_user, get_user_id

router = APIRouter(prefix="/api/v1/live", tags=["live-session"])


def _gen_room_code() -> str:
    """生成 6 位数字房间码"""
    return ''.join(random.choices(string.digits, k=6))


# ---------- Request Models ----------

class CreateSessionRequest(BaseModel):
    title: str = Field(..., max_length=200)
    knowledge_tree_id: str | None = None


class JoinSessionRequest(BaseModel):
    user_name: str = Field(default="匿名", max_length=100)


class PushQuestionRequest(BaseModel):
    question_ids: list[str] = Field(..., min_length=1, max_length=50)
    mode: str = Field(default="broadcast")  # broadcast/spotlight
    target_participant_id: str | None = None


class SubmitAnswerRequest(BaseModel):
    live_question_id: str
    user_answer: str
    time_spent_ms: int = 0


class SpotlightRequest(BaseModel):
    """随机抽查"""
    question_id: str | None = None  # None=随机从题库选
    target_participant_id: str | None = None  # None=随机选人


# ---------- 会话管理 ----------

@router.post("/sessions")
async def create_session(
    req: CreateSessionRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """创建实时会话（教师/主持人）"""
    user_id = get_user_id(current_user)
    # 生成唯一房间码
    for _ in range(10):
        code = _gen_room_code()
        existing = await db.execute(select(LiveSession).where(LiveSession.room_code == code))
        if not existing.scalar_one_or_none():
            break
    else:
        raise HTTPException(500, "房间码生成失败，请重试")

    session = LiveSession(
        id=gen_uuid(),
        host_id=user_id,
        title=req.title,
        room_code=code,
        knowledge_tree_id=req.knowledge_tree_id,
        status="waiting",
    )
    db.add(session)
    # 主持人自动成为参与者
    participant = LiveParticipant(
        id=gen_uuid(),
        session_id=session.id,
        user_id=user_id,
        user_name="主持人",
        score=0,
    )
    db.add(participant)
    await db.commit()

    return {
        "session_id": session.id,
        "room_code": code,
        "title": session.title,
        "status": session.status,
        "ws_room_id": session.id,  # WebSocket 房间 ID
    }


@router.get("/sessions/{code}")
async def get_session_by_code(
    code: str,
    db: AsyncSession = Depends(get_db),
):
    """通过房间码查询会话"""
    result = await db.execute(
        select(LiveSession).where(LiveSession.room_code == code, LiveSession.status != "ended")
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "会话不存在或已结束")

    # 获取参与者列表
    p_result = await db.execute(
        select(LiveParticipant).where(LiveParticipant.session_id == session.id)
    )
    participants = p_result.scalars().all()

    return {
        "session_id": session.id,
        "room_code": session.room_code,
        "title": session.title,
        "status": session.status,
        "host_id": session.host_id,
        "ws_room_id": session.id,
        "participants": [
            {"id": p.id, "user_id": p.user_id, "user_name": p.user_name, "score": p.score}
            for p in participants
        ],
    }


@router.post("/sessions/{session_id}/join")
async def join_session(
    session_id: str,
    req: JoinSessionRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """加入会话（学生）"""
    user_id = get_user_id(current_user)

    result = await db.execute(select(LiveSession).where(LiveSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session or session.status == "ended":
        raise HTTPException(404, "会话不存在或已结束")

    # 检查是否已加入
    existing = await db.execute(
        select(LiveParticipant).where(
            LiveParticipant.session_id == session_id,
            LiveParticipant.user_id == user_id,
        )
    )
    p = existing.scalar_one_or_none()
    if p:
        # 已加入，返回现有信息
        return {"participant_id": p.id, "user_name": p.user_name, "already_joined": True}

    participant = LiveParticipant(
        id=gen_uuid(),
        session_id=session_id,
        user_id=user_id,
        user_name=req.user_name,
    )
    db.add(participant)

    # 自动将会话状态改为 active
    if session.status == "waiting":
        session.status = "active"

    await db.commit()

    return {
        "participant_id": participant.id,
        "user_name": participant.user_name,
        "session_id": session_id,
        "ws_room_id": session_id,
    }


# ---------- 推送考题 ----------

@router.post("/sessions/{session_id}/push")
async def push_questions(
    session_id: str,
    req: PushQuestionRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """推送考题到会话"""
    user_id = get_user_id(current_user)

    result = await db.execute(select(LiveSession).where(LiveSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "会话不存在")
    if session.host_id != user_id:
        raise HTTPException(403, "只有主持人可以推送考题")

    # 获取当前最大题号
    idx_result = await db.execute(
        select(func.max(LiveQuestion.question_index)).where(LiveQuestion.session_id == session_id)
    )
    max_idx = idx_result.scalar() or 0

    questions = []
    # Batch the existence check. The loop used to issue one SELECT per submitted
    # id — bounded at 50 by the request model, but still up to 50 round trips on
    # a user-triggered endpoint. Guarded by
    # tests/test_live_session/test_push_questions_queries.py.
    wanted_ids = list(req.question_ids)
    questions_by_id: dict[str, QuestionBank] = {}
    if wanted_ids:
        q_result = await db.execute(
            select(QuestionBank).where(QuestionBank.id.in_(wanted_ids))
        )
        questions_by_id = {q.id: q for q in q_result.scalars().all()}

    for i, qid in enumerate(wanted_ids):
        # 验证题目存在
        q = questions_by_id.get(qid)
        if not q:
            continue

        lq = LiveQuestion(
            id=gen_uuid(),
            session_id=session_id,
            question_id=qid,
            question_index=max_idx + i + 1,
            mode=req.mode,
            target_participant_id=req.target_participant_id,
            status="active",
        )
        db.add(lq)
        questions.append({
            "live_question_id": lq.id,
            "question_id": qid,
            "question_index": lq.question_index,
            "question_type": q.question_type,
            "question_text": q.question_text,
            "options": q.options,
            "mode": lq.mode,
        })

    await db.commit()

    return {
        "pushed_count": len(questions),
        "questions": questions,
        "ws_room_id": session_id,
    }


@router.post("/sessions/{session_id}/spotlight")
async def spotlight_question(
    session_id: str,
    req: SpotlightRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """随机抽查：随机选人 + 随机/指定题目"""
    user_id = get_user_id(current_user)

    result = await db.execute(select(LiveSession).where(LiveSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "会话不存在")
    if session.host_id != user_id:
        raise HTTPException(403, "只有主持人可以随机抽查")

    # 随机选人（如果未指定）
    target_id = req.target_participant_id
    if not target_id:
        p_result = await db.execute(
            select(LiveParticipant).where(
                LiveParticipant.session_id == session_id,
                LiveParticipant.user_id != session.host_id,  # 排除主持人
            )
        )
        participants = p_result.scalars().all()
        if not participants:
            raise HTTPException(400, "没有可抽查的参与者")
        target = random.choice(participants)
        target_id = target.id

    # 获取题目
    qid = req.question_id
    if not qid:
        # 随机从已推送题目中选
        lq_result = await db.execute(
            select(LiveQuestion).where(LiveQuestion.session_id == session_id)
            .order_by(func.random()).limit(1)
        )
        lq = lq_result.scalar_one_or_none()
        if lq:
            qid = lq.question_id

    if not qid:
        # 从题库随机选
        qb_result = await db.execute(select(QuestionBank).order_by(func.random()).limit(1))
        qb = qb_result.scalar_one_or_none()
        if qb:
            qid = qb.id
        else:
            raise HTTPException(400, "题库为空，无法抽查")

    # 获取题目详情
    q_result = await db.execute(select(QuestionBank).where(QuestionBank.id == qid))
    q = q_result.scalar_one_or_none()
    if not q:
        raise HTTPException(404, "题目不存在")

    # 获取目标参与者信息
    tp_result = await db.execute(select(LiveParticipant).where(LiveParticipant.id == target_id))
    target_p = tp_result.scalar_one_or_none()

    idx_result = await db.execute(
        select(func.max(LiveQuestion.question_index)).where(LiveQuestion.session_id == session_id)
    )
    max_idx = idx_result.scalar() or 0

    lq = LiveQuestion(
        id=gen_uuid(),
        session_id=session_id,
        question_id=qid,
        question_index=max_idx + 1,
        mode="spotlight",
        target_participant_id=target_id,
        status="active",
    )
    db.add(lq)
    await db.commit()

    return {
        "live_question_id": lq.id,
        "question_id": qid,
        "question_text": q.question_text,
        "question_type": q.question_type,
        "options": q.options,
        "question_index": lq.question_index,
        "mode": "spotlight",
        "target_participant_id": target_id,
        "target_user_name": target_p.user_name if target_p else "未知",
        "ws_room_id": session_id,
    }


# ---------- 作答 ----------

@router.post("/sessions/{session_id}/answer")
async def submit_answer(
    session_id: str,
    req: SubmitAnswerRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """提交答案"""
    user_id = get_user_id(current_user)

    # 获取参与者
    session = await db.get(LiveSession, session_id)
    if not session:
        raise HTTPException(404, "会话不存在")
    if session.status == "ended":
        raise HTTPException(409, "会话已结束，不能继续作答")
    p_result = await db.execute(
        select(LiveParticipant).where(
            LiveParticipant.session_id == session_id,
            LiveParticipant.user_id == user_id,
        )
    )
    participant = p_result.scalar_one_or_none()
    if not participant:
        raise HTTPException(403, "你不是此会话的参与者")

    # 获取实时题目
    lq_result = await db.execute(
        select(LiveQuestion).where(LiveQuestion.id == req.live_question_id, LiveQuestion.session_id == session_id)
    )
    lq = lq_result.scalar_one_or_none()
    if not lq:
        raise HTTPException(404, "题目不存在")

    # spotlight 模式检查
    if lq.mode == "spotlight" and lq.target_participant_id != participant.id:
        raise HTTPException(403, "此题为抽查题，你不是被抽查对象")

    # 获取正确答案
    q_result = await db.execute(select(QuestionBank).where(QuestionBank.id == lq.question_id))
    q = q_result.scalar_one_or_none()

    # Reuse typed grading; an empty answer or substring isn't a correct choice.
    is_correct = False
    if q:
        from app.core.quiz import exam_engine
        is_correct = exam_engine._check_answer(req.user_answer, q.correct_answer or "", q.question_type)

    # 检查是否已作答
    existing = await db.execute(
        select(LiveAnswer).where(
            LiveAnswer.live_question_id == req.live_question_id,
            LiveAnswer.participant_id == participant.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, "你已经回答过此题")

    answer = LiveAnswer(
        id=gen_uuid(),
        live_question_id=req.live_question_id,
        participant_id=participant.id,
        user_answer=req.user_answer,
        is_correct=is_correct,
        time_spent_ms=req.time_spent_ms,
    )
    db.add(answer)

    # 更新分数
    participant.total_answered += 1
    if is_correct:
        participant.score += 10
        participant.correct_count += 1

    await db.commit()

    return {
        "answer_id": answer.id,
        "is_correct": is_correct,
        "correct_answer": q.correct_answer if q else None,
        "analysis": q.analysis if q else None,
        "score": participant.score,
    }


# ---------- 排行榜 ----------

@router.get("/sessions/{session_id}/leaderboard")
async def get_leaderboard(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """获取排行榜（仅主持人或参与者）"""
    user_id = get_user_id(current_user)

    result = await db.execute(select(LiveSession).where(LiveSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "会话不存在")
    if user_id != session.host_id:
        p_result = await db.execute(
            select(LiveParticipant).where(
                LiveParticipant.session_id == session_id,
                LiveParticipant.user_id == user_id,
            )
        )
        if not p_result.scalar_one_or_none():
            raise HTTPException(403, "你不是此会话的参与者")

    p_result = await db.execute(
        select(LiveParticipant)
        .where(LiveParticipant.session_id == session_id)
        .order_by(LiveParticipant.score.desc(), LiveParticipant.correct_count.desc())
    )
    participants = p_result.scalars().all()

    return {
        "leaderboard": [
            {
                "rank": i + 1,
                "user_name": p.user_name,
                "score": p.score,
                "correct_count": p.correct_count,
                "total_answered": p.total_answered,
                "accuracy": round(p.correct_count / max(p.total_answered, 1), 2),
            }
            for i, p in enumerate(participants)
        ]
    }


# ---------- 结束会话 ----------

@router.post("/sessions/{session_id}/end")
async def end_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """结束会话"""
    user_id = get_user_id(current_user)

    result = await db.execute(select(LiveSession).where(LiveSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "会话不存在")
    if session.host_id != user_id:
        raise HTTPException(403, "只有主持人可以结束会话")

    session.status = "ended"
    session.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()

    return {"ended": True, "session_id": session_id}


# ---------- 获取会话题目列表 ----------

@router.get("/sessions/{session_id}/questions")
async def get_session_questions(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """获取会话已推送的所有题目

    仅主持人可见正确答案；参与者只拿到题目文本与选项，避免作弊面板。
    """
    user_id = get_user_id(current_user)

    result = await db.execute(select(LiveSession).where(LiveSession.id == session_id))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(404, "会话不存在")

    # 必须是主持人或该会话的参与者
    if user_id != session.host_id:
        p_result = await db.execute(
            select(LiveParticipant).where(
                LiveParticipant.session_id == session_id,
                LiveParticipant.user_id == user_id,
            )
        )
        if not p_result.scalar_one_or_none():
            raise HTTPException(403, "你不是此会话的参与者")

    is_host = user_id == session.host_id

    lq_result = await db.execute(
        select(LiveQuestion, QuestionBank)
        .join(QuestionBank, LiveQuestion.question_id == QuestionBank.id)
        .where(LiveQuestion.session_id == session_id)
        .order_by(LiveQuestion.question_index)
    )
    rows = lq_result.all()

    return {
        "questions": [
            {
                "live_question_id": lq.id,
                "question_id": lq.question_id,
                "question_index": lq.question_index,
                "question_type": qb.question_type,
                "question_text": qb.question_text,
                "options": qb.options,
                "correct_answer": qb.correct_answer if is_host else None,
                "mode": lq.mode,
                "target_participant_id": lq.target_participant_id,
                "status": lq.status,
            }
            for lq, qb in rows
        ]
    }
