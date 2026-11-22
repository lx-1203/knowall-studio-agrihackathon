"""Research API — Deep Research endpoints."""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.core.auth import get_user_id, load_user_api_keys
from app.core.generation_access import generation_user
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("knowall.api.research")
router = APIRouter(prefix="/api/v1/research", tags=["research"])


class ResearchStartRequest(BaseModel):
    topic: str = Field(..., min_length=2, description="研究主题")
    max_subtopics: int = Field(default=8, ge=2, le=15, description="最大子课题数")

    @field_validator("topic", mode="before")
    @classmethod
    def trim_topic(cls, value):
        return value.strip() if isinstance(value, str) else value


class ResearchResponse(BaseModel):
    task_id: str
    topic: str
    status: str
    progress: float


@router.post("/start", response_model=ResearchResponse)
async def start_research(req: ResearchStartRequest, db: AsyncSession = Depends(get_db), current_user=Depends(generation_user)):
    """Start a new deep research task."""
    from app.core.agents.research_agent import research_agent

    user_id = get_user_id(current_user)
    await load_user_api_keys(user_id, db, current_user is not None)
    task = await research_agent.start_research(
        topic=req.topic,
        max_subtopics=req.max_subtopics,
        user_id=user_id,
    )

    return ResearchResponse(
        task_id=task.task_id,
        topic=task.topic,
        status=task.status.value,
        progress=task.progress,
    )


@router.get("/{task_id}/status")
async def get_research_status(task_id: str, current_user=Depends(generation_user)):
    """Get the status of a research task."""
    from app.core.agents.research_agent import research_agent

    task = research_agent.get_task(task_id, get_user_id(current_user))
    if not task:
        raise HTTPException(status_code=404, detail="Research task not found")

    return {
        "task_id": task.task_id,
        "topic": task.topic,
        "refined_topic": task.refined_topic,
        "status": task.status.value,
        "progress": task.progress,
        "sub_topics": [
            {
                "title": st.title,
                "description": st.description,
                "status": st.status,
                "finding_count": len(st.findings),
            }
            for st in task.sub_topics
        ],
        "report": task.report,
        "sources": task.sources,
        "error": task.error,
    }


@router.get("/{task_id}/stream")
async def stream_research(task_id: str, current_user=Depends(generation_user)):
    """SSE stream of research progress."""
    from app.core.agents.research_agent import research_agent

    task = research_agent.get_task(task_id, get_user_id(current_user))
    if not task:
        raise HTTPException(status_code=404, detail="Research task not found")

    async def event_generator():
        async for event in research_agent.stream_progress(task_id):
            import json
            yield f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/tasks")
async def list_research_tasks(current_user=Depends(generation_user)):
    """List all research tasks."""
    from app.core.agents.research_agent import research_agent
    return {"tasks": research_agent.list_tasks(get_user_id(current_user))}
