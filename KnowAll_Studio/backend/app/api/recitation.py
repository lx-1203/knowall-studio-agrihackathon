"""背诵/默写 API 路由"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import RecitationPassage, RecitationRecord, gen_uuid
from app.core.auth import get_optional_user, get_user_id
from app.core.recitation.diff_engine import diff_texts, compute_accuracy, generate_hide_plan
from app.core.memory import fsrs

router = APIRouter(prefix="/api/v1/recitation", tags=["recitation"])


# ---------- Request / Response Models ----------

class CreatePassageRequest(BaseModel):
    title: str = Field(..., max_length=200)
    content: str = Field(..., min_length=1, max_length=100_000)
    source: str | None = None
    category: str = "custom"
    difficulty: int = Field(default=1, ge=1, le=5)
    tags: list[str] = Field(default_factory=list)
    knowledge_point_id: str | None = None

    @field_validator('title', 'content')
    @classmethod
    def readable_text(cls, value):
        if not value.strip():
            raise ValueError('标题和背诵内容不能为空白')
        return value


class SubmitDictationRequest(BaseModel):
    passage_id: str = Field(..., max_length=64)
    user_input: str = Field(..., max_length=100_000)
    time_spent_ms: int = 0


class ReviewRequest(BaseModel):
    record_id: str = Field(..., max_length=64)
    rating: int = Field(..., ge=1, le=4)  # FSRS: 1=Again, 2=Hard, 3=Good, 4=Easy


class HidePlanRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=100_000)
    level: int = Field(default=1, ge=1, le=5)


# ---------- 段落管理 ----------

@router.post("/passages")
async def create_passage(
    req: CreatePassageRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """创建背诵段落"""
    user_id = get_user_id(current_user)
    passage = RecitationPassage(
        id=gen_uuid(),
        title=req.title,
        content=req.content,
        source=req.source,
        category=req.category,
        difficulty=req.difficulty,
        tags=req.tags,
        knowledge_point_id=req.knowledge_point_id,
        user_id=user_id,
    )
    db.add(passage)
    await db.commit()
    return {
        "id": passage.id,
        "title": passage.title,
        "content": passage.content,
        "source": passage.source,
        "category": passage.category,
        "difficulty": passage.difficulty,
        "tags": passage.tags,
        "created_at": passage.created_at.isoformat(),
    }


@router.get("/passages")
async def list_passages(
    category: str | None = None,
    difficulty: int | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """获取背诵段落列表"""
    user_id = get_user_id(current_user)
    stmt = select(RecitationPassage).where(RecitationPassage.user_id == user_id)
    if category:
        stmt = stmt.where(RecitationPassage.category == category)
    if difficulty:
        stmt = stmt.where(RecitationPassage.difficulty == difficulty)
    stmt = stmt.order_by(RecitationPassage.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    passages = result.scalars().all()

    return {
        "passages": [
            {
                "id": p.id,
                "title": p.title,
                "content": p.content,
                "source": p.source,
                "category": p.category,
                "difficulty": p.difficulty,
                "tags": p.tags,
                "created_at": p.created_at.isoformat(),
            }
            for p in passages
        ],
        "total": len(passages),
    }


@router.get("/passages/{passage_id}")
async def get_passage(
    passage_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """获取单个段落"""
    user_id = get_user_id(current_user)
    result = await db.execute(
        select(RecitationPassage).where(
            RecitationPassage.id == passage_id,
            RecitationPassage.user_id == user_id,
        )
    )
    passage = result.scalar_one_or_none()
    if not passage:
        raise HTTPException(404, "段落不存在")
    return {
        "id": passage.id,
        "title": passage.title,
        "content": passage.content,
        "source": passage.source,
        "category": passage.category,
        "difficulty": passage.difficulty,
        "tags": passage.tags,
        "created_at": passage.created_at.isoformat(),
    }


@router.delete("/passages/{passage_id}")
async def delete_passage(
    passage_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """删除段落"""
    user_id = get_user_id(current_user)
    result = await db.execute(
        select(RecitationPassage).where(
            RecitationPassage.id == passage_id,
            RecitationPassage.user_id == user_id,
        )
    )
    passage = result.scalar_one_or_none()
    if not passage:
        raise HTTPException(404, "段落不存在")
    await db.delete(passage)
    await db.commit()
    return {"deleted": True}


# ---------- 遮字背诵 ----------

@router.post("/hide-plan")
async def get_hide_plan(req: HidePlanRequest):
    """生成遮字背诵隐藏方案"""
    plan = generate_hide_plan(req.content, req.level)
    return plan


# ---------- 默写对比 ----------

@router.post("/dictation/submit")
async def submit_dictation(
    req: SubmitDictationRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """提交默写结果，返回 diff 对比"""
    user_id = get_user_id(current_user)

    # 获取原文（必须属于当前用户，防跨用户提交）
    result = await db.execute(
        select(RecitationPassage).where(
            RecitationPassage.id == req.passage_id,
            RecitationPassage.user_id == user_id,
        )
    )
    passage = result.scalar_one_or_none()
    if not passage:
        raise HTTPException(404, "段落不存在")

    # 计算 diff
    diffs = diff_texts(passage.content, req.user_input)
    accuracy = compute_accuracy(passage.content, req.user_input, diffs)

    # 创建记录（带 FSRS 初始状态）
    fsrs_state = fsrs.init_card()
    record = RecitationRecord(
        id=gen_uuid(),
        passage_id=req.passage_id,
        mode="dictation",
        accuracy=accuracy,
        user_input=req.user_input,
        diff_result=diffs,
        time_spent_ms=req.time_spent_ms,
        stability=fsrs_state["stability"],
        difficulty_score=fsrs_state["difficulty"],
        state="new",
        review_count=0,
        user_id=user_id,
    )
    db.add(record)
    await db.commit()

    return {
        "record_id": record.id,
        "accuracy": accuracy,
        "diffs": diffs,
        "original_length": len(passage.content),
        "input_length": len(req.user_input),
        "correct_chars": sum(len(d["text"]) for d in diffs if d["type"] == "equal"),
        "missing_chars": sum(len(d["text"]) for d in diffs if d["type"] == "delete"),
        "extra_chars": sum(len(d["text"]) for d in diffs if d["type"] == "insert"),
    }


# ---------- FSRS 复习 ----------

@router.post("/review")
async def review_passage(
    req: ReviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """对背诵记录进行 FSRS 复习评级"""
    user_id = get_user_id(current_user)
    result = await db.execute(
        select(RecitationRecord).where(
            RecitationRecord.id == req.record_id,
            RecitationRecord.user_id == user_id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise HTTPException(404, "记录不存在")

    # 用 FSRS 引擎计算下次复习时间
    card_state = {
        "stability": record.stability or 0.0,
        "difficulty": record.difficulty_score or 0.0,
        "retrievability": 0.0,
        "next_review_at": record.next_review_at,
        "last_review_at": record.last_review_at or record.created_at,
        "fsrs_version": record.fsrs_version,
        "review_count": record.review_count or 0,
        "state": record.state or "new",
    }
    new_state = fsrs.review(card_state, req.rating)

    # 更新记录
    record.stability = new_state["stability"]
    record.difficulty_score = new_state["difficulty"]
    record.state = new_state["state"]
    record.next_review_at = new_state.get("next_review_at")
    record.review_count = new_state["review_count"]
    record.fsrs_version = new_state["fsrs_version"]
    record.last_review_at = new_state["last_review_at"]
    await db.commit()

    return {
        "record_id": record.id,
        "state": new_state["state"],
        "stability": new_state["stability"],
        "difficulty": new_state["difficulty"],
        "next_review_at": new_state.get("next_review_at").isoformat() if new_state.get("next_review_at") else None,
        "review_count": new_state["review_count"],
    }


@router.get("/due")
async def get_due_passages(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """获取需要复习的背诵段落"""
    user_id = get_user_id(current_user)
    now_time = datetime.now(timezone.utc).replace(tzinfo=None)

    # 查找到期的记录
    stmt = (
        select(RecitationRecord, RecitationPassage)
        .join(RecitationPassage, RecitationRecord.passage_id == RecitationPassage.id)
        .where(
            RecitationRecord.user_id == user_id,
            RecitationRecord.next_review_at != None,  # noqa: E711
            RecitationRecord.next_review_at <= now_time,
        )
        .order_by(RecitationRecord.next_review_at)
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.all()

    return {
        "due_items": [
            {
                "record_id": record.id,
                "passage_id": passage.id,
                "passage_title": passage.title,
                "passage_content": passage.content,
                "mode": record.mode,
                "accuracy": record.accuracy,
                "review_count": record.review_count,
                "next_review_at": record.next_review_at.isoformat() if record.next_review_at else None,
                "state": record.state,
            }
            for record, passage in rows
        ],
        "total": len(rows),
    }


# ---------- 统计 ----------

@router.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    """获取背诵统计"""
    user_id = get_user_id(current_user)

    # 段落数
    stmt = select(func.count()).select_from(RecitationPassage).where(
        RecitationPassage.user_id == user_id
    )
    total_passages = (await db.execute(stmt)).scalar() or 0

    # 记录数
    stmt = select(func.count()).select_from(RecitationRecord).where(
        RecitationRecord.user_id == user_id
    )
    total_records = (await db.execute(stmt)).scalar() or 0

    # 平均准确率
    stmt = select(func.avg(RecitationRecord.accuracy)).where(
        RecitationRecord.user_id == user_id
    )
    avg_accuracy = (await db.execute(stmt)).scalar() or 0.0

    # 已掌握（state == "review" 且 accuracy >= 0.9）
    stmt = (
        select(func.count())
        .select_from(RecitationRecord)
        .where(
            RecitationRecord.user_id == user_id,
            RecitationRecord.state == "review",
            RecitationRecord.accuracy >= 0.9,
        )
    )
    mastered = (await db.execute(stmt)).scalar() or 0

    return {
        "total_passages": total_passages,
        "total_records": total_records,
        "avg_accuracy": round(avg_accuracy, 4),
        "mastered": mastered,
    }
