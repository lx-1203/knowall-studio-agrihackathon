"""Agent orchestration API routes"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from app.database import get_db
from app.core.auth import get_user_id, load_user_api_keys
from app.core.documents import assert_document_access
from app.core.derived_document_access import require_derived_access
from app.models import KnowledgeSummary
from app.api.study import study_user
from app.core.agents.orchestrator import orchestrator

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


class OrchestrateRequest(BaseModel):
    summary_id: str
    document_id: str
    agents: list[str] | None = None
    language_type: str | None = None
    config: dict | None = None


async def _require_sources(req: OrchestrateRequest, db: AsyncSession):
    # These legacy agents persist globally readable derivatives. Private sources
    # must use the owner-scoped knowledge network or personal learning flows.
    summary = await db.get(KnowledgeSummary, req.summary_id, populate_existing=True)
    await require_derived_access(db, summary, None)
    if await assert_document_access(db, req.document_id, None) is None:
        raise HTTPException(404, 'Document not found')


@router.post("/orchestrate")
async def orchestrate_agents(
    req: OrchestrateRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(study_user),
):
    """Run multiple agents in parallel. Returns complete results."""
    await _require_sources(req, db)
    await load_user_api_keys(get_user_id(current_user), db, current_user is not None)

    result = await orchestrator.orchestrate(
        summary_id=req.summary_id,
        document_id=req.document_id,
        agent_names=req.agents,
        config={
            **(req.config or {}),
            "language_type": req.language_type,
            "user_id": get_user_id(current_user),
        },
    )
    return result


@router.post("/orchestrate/stream")
async def orchestrate_agents_stream(
    req: OrchestrateRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(study_user),
):
    """Run multiple agents with SSE streaming progress."""
    await _require_sources(req, db)
    await load_user_api_keys(get_user_id(current_user), db, current_user is not None)

    async def event_stream():
        async for event in orchestrator.orchestrate_sse(
            summary_id=req.summary_id,
            document_id=req.document_id,
            agent_names=req.agents,
            config={
                **(req.config or {}),
                "language_type": req.language_type,
                "user_id": get_user_id(current_user),
            },
        ):
            yield event

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/list")
async def list_agents():
    """List all registered agents and their descriptions."""
    from app.core.agents.base import AgentRegistry

    agents_list = []
    for name in AgentRegistry.list_all():
        agent_cls = AgentRegistry.get(name)
        agents_list.append({
            "name": name,
            "description": agent_cls.description if agent_cls else "",
        })
    return {"agents": agents_list}
