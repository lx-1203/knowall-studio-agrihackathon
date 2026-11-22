"""Study Plan API routes - learning roadmap, goals, and reminders"""
from datetime import datetime, timezone, timedelta
from typing import Literal
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, or_
from pydantic import BaseModel, Field, field_validator
from app.database import get_db
from app.models import StudyPlan, StudyGoal, StudyReminder
from app.core.auth import get_current_user, get_user_id, load_user_api_keys, security
from app.core.documents import assert_document_access
from app.config import settings
from app.core.agents.study_plan_agent import StudyPlanOptions


def _parse_iso(date_str: str | None):
    """Safely parse an ISO datetime string, returning None for empty/invalid input."""
    if not date_str:
        return None
    try:
        parsed = datetime.fromisoformat(date_str)
        return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed
    except (ValueError, TypeError, OverflowError):
        return None

router = APIRouter(prefix="/api/v1/study", tags=["study"])


async def study_user(credentials=Depends(security), db: AsyncSession=Depends(get_db)):
    # Preserve local anonymous plans, but an invalid presented token must not
    # silently become local_user. Desktop login mode always requires identity.
    if credentials is None and not settings.desktop_require_login:
        return None
    return await get_current_user(credentials, db)


async def owned_plan(plan_id: str, user_id: str, db: AsyncSession):
    plan = await db.scalar(select(StudyPlan).where(StudyPlan.id == plan_id, StudyPlan.user_id == user_id))
    if not plan:
        raise HTTPException(404, "Study plan not found")
    return plan


async def owned_goal(goal_id: str, user_id: str, db: AsyncSession):
    goal = await db.scalar(select(StudyGoal).join(StudyPlan, StudyGoal.plan_id == StudyPlan.id).where(StudyGoal.id == goal_id, StudyPlan.user_id == user_id))
    if not goal:
        raise HTTPException(404, "Goal not found")
    return goal


async def recalculate_progress(plan: StudyPlan, db: AsyncSession):
    await db.flush()
    total = await db.scalar(select(func.count(StudyGoal.id)).where(StudyGoal.plan_id == plan.id)) or 0
    completed = await db.scalar(select(func.count(StudyGoal.id)).where(StudyGoal.plan_id == plan.id, StudyGoal.completed == True)) or 0
    plan.progress = round(completed / total * 100, 1) if total else 0
    if total and completed == total:
        plan.status = "completed"
    elif plan.status == "completed":
        plan.status = "active"
    plan.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)


class GoalFields(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    priority: Literal['high', 'medium', 'low'] = 'medium'
    due_date: str | None = None

    @field_validator('title', mode='before')
    @classmethod
    def trim_title(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('due_date')
    @classmethod
    def valid_date(cls, value):
        if value is not None and _parse_iso(value) is None:
            raise ValueError('日期格式不正确')
        return value


class GeneratePlanRequest(BaseModel):
    knowledge_text: str = ""
    document_id: str | None = None
    plan_duration_weeks: int = Field(default=4, ge=1, le=52)
    model: str = "deepseek-chat"


class CreatePlanRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    target_end_date: str | None = None
    goals: list[GoalFields] = Field(default_factory=list)

    @field_validator('name', mode='before')
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('target_end_date')
    @classmethod
    def valid_date(cls, value):
        return GoalFields.valid_date(value)


class CreateGoalRequest(GoalFields):
    plan_id: str


class UpdatePlanRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    status: Literal['active', 'completed', 'paused'] | None = None
    progress: float | None = Field(default=None, ge=0, le=100)
    target_end_date: str | None = None

    @field_validator('name', 'description', 'status', 'progress', mode='before')
    @classmethod
    def non_null_value(cls, value):
        if value is None:
            raise ValueError('字段不能为 null')
        return value

    @field_validator('name', mode='before')
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('target_end_date')
    @classmethod
    def valid_date(cls, value):
        return GoalFields.valid_date(value)


class CreateReminderRequest(BaseModel):
    plan_id: str | None = None
    goal_id: str | None = None
    message: str = Field(min_length=1)
    remind_at: str
    repeat_daily: bool = False


# ===== Study Plans =====

@router.post("/generate-plan")
async def generate_study_plan(
    req: GeneratePlanRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(study_user),
):
    """AI generates a structured study plan from knowledge text or document."""
    user_id = get_user_id(current_user)
    knowledge_text = req.knowledge_text

    # If document_id provided, fetch chunks
    if req.document_id and not knowledge_text:
        from sqlalchemy import select
        from app.models import DocumentChunk
        doc = await assert_document_access(db, req.document_id, current_user.id if current_user else None)
        if not doc:
            raise HTTPException(404, "Document not found")
        result = await db.execute(
            select(DocumentChunk)
            .where(DocumentChunk.doc_id == req.document_id)
            .order_by(DocumentChunk.chunk_index)
        )
        chunks = result.scalars().all()
        knowledge_text = "\n\n".join(c.text_content for c in chunks)
        # Cap at token budget
        MAX_CHARS = 4000
        if len(knowledge_text) > MAX_CHARS:
            knowledge_text = knowledge_text[:MAX_CHARS] + "\n\n...(content truncated)"

    if not knowledge_text.strip():
        raise HTTPException(400, "Please provide knowledge_text or document_id")

    await load_user_api_keys(user_id, db, current_user is not None)
    import json
    from app.core.assistant import assistant

    prompt = f"""你是一位学习规划专家。请根据以下知识点内容，制定一份{req.plan_duration_weeks}周的学习计划。

知识点内容：
{knowledge_text[:4000]}

请输出严格JSON格式的学习计划，包含计划名称、整体描述、按周拆分的学习目标（每个目标含标题和优先级high/medium/low）：

{{"name": "计划名称", "description": "整体描述", "goals": [{{"title": "目标标题", "priority": "high/medium/low", "week": 1}}]}}"""

    try:
        response = await assistant.chat(prompt, "tutor", None, req.model, user_id=user_id)
        data = CreatePlanRequest.model_validate(json.loads(response))
        if not data.goals:
            raise ValueError('生成的学习计划没有有效目标')
    except Exception as e:
        raise HTTPException(500, f"AI规划生成失败: {str(e)}")

    # Create plan with goals
    plan = StudyPlan(
        user_id=user_id,
        name=data.name,
        description=data.description,
    )
    db.add(plan)
    await db.flush()

    goal_count = 0
    for i, g in enumerate(data.goals):
        goal = StudyGoal(
            plan_id=plan.id,
            title=g.title,
            description=g.description,
            priority=g.priority,
            due_date=_parse_iso(g.due_date),
            order_index=i,
        )
        db.add(goal)
        goal_count += 1

    await db.commit()
    return {"plan_id": plan.id, "name": plan.name, "goal_count": goal_count}


@router.post("/plans")
async def create_plan(
    req: CreatePlanRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(study_user),
):
    """Create a new study plan with optional goals."""
    user_id = get_user_id(current_user)
    plan = StudyPlan(
        user_id=user_id,
        name=req.name,
        description=req.description,
        target_end_date=_parse_iso(req.target_end_date),
    )
    db.add(plan)
    await db.flush()

    for i, g in enumerate(req.goals or []):
        goal = StudyGoal(
            plan_id=plan.id,
            title=g.title,
            description=g.description,
            priority=g.priority,
            due_date=_parse_iso(g.due_date),
            order_index=i,
        )
        db.add(goal)

    await db.commit()
    return {"plan_id": plan.id, "name": plan.name, "goal_count": len(req.goals or [])}


@router.get("/plans")
async def list_plans(
    limit: int = 1000,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(study_user),
):
    """List all study plans with progress."""
    result = await db.execute(
        select(StudyPlan).where(StudyPlan.user_id == get_user_id(current_user)).order_by(StudyPlan.updated_at.desc()).offset(offset).limit(limit)
    )
    plans = result.scalars().all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "progress": p.progress,
            "status": p.status,
            "target_end_date": p.target_end_date.isoformat() if p.target_end_date else None,
            "created_at": p.created_at.isoformat(),
        }
        for p in plans
    ]


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(study_user)):
    """Get a study plan with all its goals."""
    plan = await owned_plan(plan_id, get_user_id(current_user), db)

    result = await db.execute(
        select(StudyGoal).where(StudyGoal.plan_id == plan_id).order_by(StudyGoal.order_index)
    )
    goals = result.scalars().all()

    return {
        "id": plan.id,
        "name": plan.name,
        "plan_type": plan.plan_type,
        "daily_hours": plan.daily_hours,
        "knowledge_point_ids": plan.knowledge_point_ids,
        "short_term_plan": plan.short_term_plan,
        "long_term_plan": plan.long_term_plan,
        "ebbinghaus_nodes": plan.ebbinghaus_nodes,
        "description": plan.description,
        "progress": plan.progress,
        "status": plan.status,
        "target_end_date": plan.target_end_date.isoformat() if plan.target_end_date else None,
        "created_at": plan.created_at.isoformat(),
        "goals": [
            {
                "id": g.id,
                "title": g.title,
                "description": g.description,
                "priority": g.priority,
                "completed": g.completed,
                "completed_at": g.completed_at.isoformat() if g.completed_at else None,
                "due_date": g.due_date.isoformat() if g.due_date else None,
                "order_index": g.order_index,
            }
            for g in goals
        ],
    }


@router.put("/plans/{plan_id}")
async def update_plan(plan_id: str, req: UpdatePlanRequest, db: AsyncSession = Depends(get_db), current_user=Depends(study_user)):
    """Update a study plan."""
    plan = await owned_plan(plan_id, get_user_id(current_user), db)
    data = req.model_dump(exclude_unset=True)

    if "name" in data:
        plan.name = data["name"]
    if "description" in data:
        plan.description = data["description"]
    if "status" in data:
        plan.status = data["status"]
    if "progress" in data:
        plan.progress = data["progress"]
    if "target_end_date" in data:
        plan.target_end_date = _parse_iso(data["target_end_date"])

    await db.commit()
    return {"status": "updated", "plan_id": plan.id}


@router.delete("/plans/{plan_id}")
async def delete_plan(plan_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(study_user)):
    """Delete a study plan and all its goals."""
    plan = await owned_plan(plan_id, get_user_id(current_user), db)
    goal_ids = select(StudyGoal.id).where(StudyGoal.plan_id == plan.id)
    await db.execute(delete(StudyReminder).where(or_(StudyReminder.plan_id == plan.id, StudyReminder.goal_id.in_(goal_ids))))
    await db.execute(delete(StudyGoal).where(StudyGoal.plan_id == plan.id))
    await db.delete(plan)
    await db.commit()
    return {"status": "deleted"}


# ===== Goals =====

@router.post("/goals")
async def create_goal(req: CreateGoalRequest, db: AsyncSession = Depends(get_db), current_user=Depends(study_user)):
    """Add a goal to a study plan."""
    plan = await owned_plan(req.plan_id, get_user_id(current_user), db)

    # Get max order_index (use MAX, not COUNT - deleted goals leave gaps)
    order_result = await db.execute(
        select(func.coalesce(func.max(StudyGoal.order_index), -1)).where(StudyGoal.plan_id == req.plan_id)
    )
    max_order = order_result.scalar() + 1

    goal = StudyGoal(
        plan_id=req.plan_id,
        title=req.title,
        description=req.description,
        priority=req.priority,
        due_date=_parse_iso(req.due_date),
        order_index=max_order,
    )
    db.add(goal)
    await recalculate_progress(plan, db)
    await db.commit()
    return {"goal_id": goal.id, "title": goal.title}


@router.put("/goals/{goal_id}/toggle")
async def toggle_goal(goal_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(study_user)):
    """Toggle goal completion status."""
    goal = await owned_goal(goal_id, get_user_id(current_user), db)

    goal.completed = not goal.completed
    goal.completed_at = datetime.now(timezone.utc).replace(tzinfo=None) if goal.completed else None
    plan = await owned_plan(goal.plan_id, get_user_id(current_user), db)
    await recalculate_progress(plan, db)

    await db.commit()
    return {"goal_id": goal.id, "completed": goal.completed, "plan_progress": plan.progress}


@router.delete("/goals/{goal_id}")
async def delete_goal(goal_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(study_user)):
    """Delete a goal."""
    goal = await owned_goal(goal_id, get_user_id(current_user), db)
    plan = await owned_plan(goal.plan_id, get_user_id(current_user), db)
    await db.execute(delete(StudyReminder).where(StudyReminder.goal_id == goal.id))
    await db.delete(goal)
    await recalculate_progress(plan, db)
    await db.commit()
    return {"status": "deleted"}


# ===== Reminders =====

@router.post("/reminders")
async def create_reminder(
    req: CreateReminderRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(study_user),
):
    """Create a study reminder."""
    user_id = get_user_id(current_user)
    remind_at = _parse_iso(req.remind_at)
    if remind_at is None:
        raise HTTPException(400, "提醒时间格式不正确，请使用ISO格式")
    plan_id = req.plan_id
    if plan_id:
        await owned_plan(plan_id, user_id, db)
    if req.goal_id:
        goal = await owned_goal(req.goal_id, user_id, db)
        if plan_id and goal.plan_id != plan_id:
            raise HTTPException(400, "提醒的目标不属于所选计划")
        plan_id = goal.plan_id
    reminder = StudyReminder(
        user_id=user_id,
        plan_id=plan_id,
        goal_id=req.goal_id,
        message=req.message,
        remind_at=remind_at,
        repeat_daily=req.repeat_daily,
    )
    db.add(reminder)
    await db.commit()
    return {"reminder_id": reminder.id, "message": reminder.message}


@router.get("/reminders/due")
async def get_due_reminders(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(study_user),
):
    """Get reminders that are due now and unread."""
    user_id = get_user_id(current_user)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result = await db.execute(
        select(StudyReminder)
        .where(StudyReminder.user_id == user_id, StudyReminder.remind_at <= now, StudyReminder.is_read == False)
        .order_by(StudyReminder.remind_at)
    )
    reminders = result.scalars().all()
    return {
        "count": len(reminders),
        "reminders": [
            {
                "id": r.id,
                "message": r.message,
                "remind_at": r.remind_at.isoformat(),
                "is_read": r.is_read,
                "repeat_daily": r.repeat_daily,
                "plan_id": r.plan_id,
                "goal_id": r.goal_id,
            }
            for r in reminders
        ],
    }


@router.post("/reminders/{reminder_id}/read")
async def mark_reminder_read(reminder_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(study_user)):
    """Mark a reminder as read."""
    result = await db.execute(select(StudyReminder).where(StudyReminder.id == reminder_id, StudyReminder.user_id == get_user_id(current_user)))
    rem = result.scalar_one_or_none()
    if not rem:
        raise HTTPException(404, "Reminder not found")
    if rem.repeat_daily:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if rem.remind_at <= now:
            days = (now - rem.remind_at).days + 1
            rem.remind_at += timedelta(days=days)
        rem.is_read = False
    else:
        rem.is_read = True
    await db.commit()
    return {"status": "read"}


class GenerateEnhancedPlanRequest(StudyPlanOptions):
    summary_id: str = Field(min_length=1)

    @field_validator('summary_id', mode='before')
    @classmethod
    def trim_summary_id(cls, value):
        return value.strip() if isinstance(value, str) else value


@router.post("/generate-plan-enhanced")
async def generate_plan_enhanced(
    req: GenerateEnhancedPlanRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(study_user),
):
    """Generate enhanced study plan with Ebbinghaus review nodes."""
    from app.core.agents.study_plan_agent import StudyPlanAgent

    config = {
        "study_plan": {
            "type": req.plan_type,
            "daily_hours": req.daily_hours,
            "ebbinghaus_enabled": req.ebbinghaus_enabled,
        },
        "start_date": req.start_date,
    }

    # Get document_id from summary
    from app.models import KnowledgeSummary
    summary = await db.get(KnowledgeSummary, req.summary_id)
    if not summary:
        raise HTTPException(404, "Summary not found")
    for document_id in dict.fromkeys([summary.document_id, *(summary.document_ids or [])]):
        if await assert_document_access(db, document_id, current_user.id if current_user else None) is None:
            raise HTTPException(404, "Document not found")
    await load_user_api_keys(get_user_id(current_user), db, current_user is not None)

    agent = StudyPlanAgent()
    result = await agent.run(
        summary_id=req.summary_id,
        document_id=summary.document_id,
        config=config,
        user_id=get_user_id(current_user),
    )

    if result.status == "error":
        raise HTTPException(500, result.error or "Plan generation failed")

    return result


@router.get("/plans/{plan_id}/ebbinghaus")
async def get_plan_ebbinghaus(
    plan_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(study_user),
):
    """Get Ebbinghaus review nodes for a study plan."""
    from app.models import StudyPlan
    plan = await owned_plan(plan_id, get_user_id(current_user), db)

    return {
        "plan_id": plan.id,
        "ebbinghaus_nodes": plan.ebbinghaus_nodes,
    }
