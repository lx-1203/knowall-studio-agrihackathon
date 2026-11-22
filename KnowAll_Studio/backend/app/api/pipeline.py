"""Pipeline API - one-click full chain automation"""
import json
import asyncio
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.core.pipeline import pipeline, PipelineStage
from app.core.model_connections import choose_request_model
from app.core.auth import get_user_id, load_user_api_keys
from app.api.study import study_user

router = APIRouter(prefix="/api/v1/pipeline", tags=["pipeline"])

# Track active pipeline runs for cancellation
_active_pipelines: dict[str, asyncio.Event] = {}


class PipelineRequest(BaseModel):
    document_id: str
    model: str = "deepseek-chat"
    question_count: int = Field(default=15, ge=5, le=50)
    question_type: str = "single_choice"
    difficulty: str = "medium"
    card_count: int = Field(default=20, ge=5, le=100)
    card_type: str = "qa"


@router.post("/run")
async def run_pipeline(
    req: PipelineRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(study_user),
):
    """Run the full pipeline and return final result (non-streaming)."""
    await load_user_api_keys(get_user_id(current_user), db, current_user is not None)
    if current_user is not None:
        await choose_request_model(req, get_user_id(current_user), db)
    final_state = None
    async for state in pipeline.run_full_chain(
        document_id=req.document_id,
        model=req.model,
        question_count=req.question_count,
        question_type=req.question_type,
        difficulty=req.difficulty,
        card_count=req.card_count,
        card_type=req.card_type,
        user_id=get_user_id(current_user),
    ):
        final_state = state

    if not final_state or final_state.stage == PipelineStage.ERROR:
        raise HTTPException(500, final_state.error if final_state else "Unknown error")

    return {
        "status": "completed",
        **final_state.result,
    }


@router.post("/run/stream")
async def run_pipeline_stream(
    req: PipelineRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(study_user),
):
    """Run pipeline with SSE progress streaming. Supports cancellation."""
    user_id = get_user_id(current_user)
    await load_user_api_keys(user_id, db, current_user is not None)
    if current_user is not None:
        await choose_request_model(req, user_id, db)

    # Create cancel event for this pipeline run
    cancel_event = asyncio.Event()
    run_id = f"{user_id}_{req.document_id}"
    if run_id in _active_pipelines:
        raise HTTPException(409, "该文档的流水线正在运行")
    _active_pipelines[run_id] = cancel_event

    async def event_stream():
        iterator = pipeline.run_full_chain(
            document_id=req.document_id, model=req.model,
            question_count=req.question_count, question_type=req.question_type,
            difficulty=req.difficulty, card_count=req.card_count,
            card_type=req.card_type, cancel_event=cancel_event, user_id=user_id,
        )
        progress = 0
        try:
            async for state in iterator:
                progress = state.progress_pct
                data = {
                    "stage": state.stage.value,
                    "progress": state.progress_pct,
                    "message": state.message,
                    "error": state.error,
                    "result": state.result,
                }
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            if not cancel_event.is_set():
                raise
            yield f"data: {json.dumps({'stage': 'cancelled', 'progress': progress, 'message': '流水线已取消', 'error': None, 'result': None}, ensure_ascii=False)}\n\n"
        finally:
            cancel_event.set()
            try:
                await iterator.aclose()
            finally:
                if _active_pipelines.get(run_id) is cancel_event:
                    _active_pipelines.pop(run_id, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/cancel")
async def cancel_pipeline(
    document_id: str,
    current_user = Depends(study_user),
):
    """Cancel a running pipeline for the current user and document."""
    user_id = get_user_id(current_user)
    run_id = f"{user_id}_{document_id}"
    cancel_event = _active_pipelines.get(run_id)
    if cancel_event:
        cancel_event.set()
        return {"status": "cancelled", "run_id": run_id}
    return {"status": "not_found", "detail": "没有找到正在运行的流水线"}


@router.get("/active")
async def list_active_pipelines(current_user=Depends(study_user)):
    """List currently active pipeline runs."""
    prefix = get_user_id(current_user) + '_'
    runs = [run_id for run_id in _active_pipelines if run_id.startswith(prefix)]
    return {"active_runs": runs, "count": len(runs)}
