"""AI Assistant chat API routes with SSE streaming support"""
import json
from fastapi import APIRouter, HTTPException, Depends, Query, Request
from app.core.streaming import ClosingStreamingResponse as StreamingResponse, closing_stream
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database import get_db
from app.core.utc_time import utc_iso
from app.models import Conversation, Message
from app.core.assistant import assistant as ai_assistant
from app.core.rag_assistant import rag_assistant
from app.core.rag import RetrievalUnavailableError
from app.core.model_connections import choose_request_model
from app.core.auth import get_current_user, get_user_id, load_user_api_keys, security
from app.config import settings

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])


async def chat_user(request: Request, credentials=Depends(security), db: AsyncSession=Depends(get_db)):
    # Only absent credentials in local anonymous mode may access local history.
    # Invalid/unsupported credentials must never fall back to local_user.
    if 'authorization' not in request.headers and credentials is None and not settings.desktop_require_login:
        return None
    return await get_current_user(credentials, db)


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    role_preset: str = "tutor"
    model: str = "deepseek-chat"
    knowledge_context: str | None = None


@router.post("/assistant")
async def chat(
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(chat_user),
):
    """Non-streaming chat: send message, get full response."""
    user_id = get_user_id(current_user)
    await load_user_api_keys(user_id, db, current_user is not None)
    if current_user is not None:
        await choose_request_model(req, user_id, db)

    # Generation quota/cache write through another session in the same workspace.
    # Read owned history first, then release the transaction before generation.
    history = []
    if req.conversation_id:
        conv = await _get_or_create_conversation(db, req, user_id)
        history = await _get_history(db, conv.id)
    await db.rollback()

    try:
        if req.knowledge_context:
            response_text = await ai_assistant.chat_with_context(
                req.message, req.knowledge_context, req.role_preset, history, req.model, user_id=user_id
            )
        else:
            response_text = await ai_assistant.chat(
                req.message, req.role_preset, history, req.model, user_id=user_id
            )
    except Exception as e:
        raise HTTPException(500, f"Chat failed: {str(e)}")

    # Recheck ownership after the model await; a deleted conversation stays gone.
    # Both messages and a new conversation persist only with a complete answer.
    conv = await _get_or_create_conversation(db, req, user_id)
    user_msg = Message(conversation_id=conv.id, role="user", content=req.message)
    db.add(user_msg)
    assistant_msg = Message(conversation_id=conv.id, role="assistant", content=response_text)
    db.add(assistant_msg)
    await db.commit()
    await db.refresh(conv)

    return {
        "conversation_id": conv.id,
        "role_preset": conv.role_preset,
        "message": response_text,
    }


@router.post("/assistant/stream")
async def chat_stream(
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(chat_user),
):
    """SSE streaming chat: events stream real-time tokens."""
    user_id = get_user_id(current_user)
    await load_user_api_keys(user_id, db, current_user is not None)
    if current_user is not None:
        await choose_request_model(req, user_id, db)

    conv = await _get_or_create_conversation(db, req, user_id)

    # Save user message
    user_msg = Message(conversation_id=conv.id, role="user", content=req.message)
    db.add(user_msg)
    await db.flush()
    await db.commit()

    # Get history
    history = await _get_history(db, conv.id)

    async def event_generator():
        from app.database import async_session
        import asyncio as _asyncio
        full_response = ""
        last_heartbeat = _asyncio.get_event_loop().time()
        try:
            if req.knowledge_context:
                stream = ai_assistant.chat_stream_with_context(
                    req.message, req.knowledge_context, req.role_preset, history, req.model, user_id=user_id
                )
            else:
                stream = ai_assistant.chat_stream(
                    req.message, req.role_preset, history, req.model, user_id=user_id
                )
            async with closing_stream(stream) as owned_stream:
                async for chunk in owned_stream:
                    full_response += chunk
                    now = _asyncio.get_event_loop().time()
                    # Send heartbeat every 15s to keep connection alive
                    if now - last_heartbeat > 15:
                        yield f": heartbeat\n\n"
                        last_heartbeat = now
                    yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
            # Save full response with independent session
            async with async_session() as save_db:
                assistant_msg = Message(
                    conversation_id=conv.id, role="assistant", content=full_response
                )
                save_db.add(assistant_msg)
                await save_db.commit()
            yield f"data: {json.dumps({'token': '', 'done': True, 'conversation_id': conv.id})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/conversations")
async def list_conversations(db: AsyncSession = Depends(get_db), limit: int = 50, offset: int = 0, current_user=Depends(chat_user)):
    """List conversations with pagination."""
    result = await db.execute(
        select(Conversation).where(Conversation.user_id == get_user_id(current_user)).order_by(Conversation.created_at.desc()).offset(offset).limit(limit)
    )
    convs = result.scalars().all()
    return [
        {"id": c.id, "title": c.title, "role_preset": c.role_preset, "created_at": utc_iso(c.created_at)}
        for c in convs
    ]


@router.get("/conversations/{conv_id}")
async def get_conversation(conv_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(chat_user)):
    """Get conversation with all messages."""
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == get_user_id(current_user)))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    result = await db.execute(
        select(Message).where(Message.conversation_id == conv_id).order_by(Message.created_at)
    )
    messages = result.scalars().all()
    return {
        "conversation_id": conv.id, "title": conv.title, "role_preset": conv.role_preset,
        "messages": [{"id": m.id, "role": m.role, "content": m.content, "created_at": utc_iso(m.created_at)} for m in messages],
    }


@router.get("/roles")
async def list_roles():
    return ai_assistant.get_role_presets()


class RAGChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    role_preset: str = "tutor"
    model: str = "deepseek-chat"
    top_k: int = 3


@router.post("/assistant/rag")
async def chat_with_rag(
    req: RAGChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(chat_user),
):
    """Chat with RAG: searches local documents and grounds answers in retrieved context."""
    user_id = get_user_id(current_user)
    await load_user_api_keys(user_id, db, current_user is not None)
    if current_user is not None:
        await choose_request_model(req, user_id, db)

    conv = await _get_or_create_conversation(db, ChatRequest(
        message=req.message, conversation_id=req.conversation_id,
        role_preset=req.role_preset, model=req.model,
    ), user_id)
    user_msg = Message(conversation_id=conv.id, role="user", content=req.message)
    db.add(user_msg)
    await db.flush()
    history = await _get_history(db, conv.id)

    try:
        response_text = await rag_assistant.chat_with_rag(
            req.message, req.role_preset, history, req.model, req.top_k, user_id=user_id,
            mode='rag' if settings.rag_provider == 'chromadb' else settings.rag_provider,
        )
    except Exception as e:
        raise HTTPException(500, f"RAG chat failed: {str(e)}")

    assistant_msg = Message(conversation_id=conv.id, role="assistant", content=response_text)
    db.add(assistant_msg)
    await db.commit()
    return {"conversation_id": conv.id, "role_preset": conv.role_preset, "message": response_text}


@router.post("/assistant/rag/stream")
async def chat_rag_stream(
    req: RAGChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(chat_user),
):
    """SSE streaming version of RAG chat."""
    user_id = get_user_id(current_user)
    await load_user_api_keys(user_id, db, current_user is not None)
    if current_user is not None:
        await choose_request_model(req, user_id, db)

    conv = await _get_or_create_conversation(db, ChatRequest(
        message=req.message, conversation_id=req.conversation_id,
        role_preset=req.role_preset, model=req.model,
    ), user_id)
    user_msg = Message(conversation_id=conv.id, role="user", content=req.message)
    db.add(user_msg)
    await db.flush()
    await db.commit()
    history = await _get_history(db, conv.id)

    async def event_generator():
        from app.database import async_session
        import asyncio as _asyncio
        full = ""
        last_hb = _asyncio.get_event_loop().time()
        try:
            async with closing_stream(rag_assistant.chat_stream_with_rag(req.message, req.role_preset, history, req.model, req.top_k, user_id=user_id, mode='rag' if settings.rag_provider == 'chromadb' else settings.rag_provider)) as owned_stream:
                async for chunk in owned_stream:
                    full += chunk
                    now = _asyncio.get_event_loop().time()
                    if now - last_hb > 15:
                        yield f": heartbeat\n\n"
                        last_hb = now
                    yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
            # Save full response with independent session (RAG)
            async with async_session() as save_db:
                save_db.add(Message(conversation_id=conv.id, role="assistant", content=full))
                await save_db.commit()
            yield f"data: {json.dumps({'token': '', 'done': True, 'conversation_id': conv.id})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/search")
async def search_documents(query: str, top_k: int = 5):
    """Search documents without AI (returns raw chunks)."""
    results = await rag_assistant.search_only(query, top_k)
    return {"query": query, "results": results, "count": len(results)}


# ===== GraphRAG Endpoints (hybrid vector + knowledge graph retrieval) =====

class GraphRAGChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    role_preset: str = "tutor"
    model: str = "deepseek-chat"
    top_k: int = 8
    max_hops: int = 2


@router.post("/assistant/graphrag")
async def chat_with_graphrag(
    req: GraphRAGChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(chat_user),
):
    """Chat with GraphRAG: hybrid vector + knowledge graph retrieval for richer context."""
    user_id = get_user_id(current_user)
    await load_user_api_keys(user_id, db, current_user is not None)
    if current_user is not None:
        await choose_request_model(req, user_id, db)

    conv = await _get_or_create_conversation(db, ChatRequest(
        message=req.message, conversation_id=req.conversation_id,
        role_preset=req.role_preset, model=req.model,
    ), user_id)
    user_msg = Message(conversation_id=conv.id, role="user", content=req.message)
    db.add(user_msg)
    await db.flush()
    history = await _get_history(db, conv.id)

    try:
        response_text = await rag_assistant.chat_with_rag(
            req.message, req.role_preset, history, req.model,
            req.top_k, user_id=user_id, mode="graphrag",
        )
    except Exception as e:
        raise HTTPException(500, f"GraphRAG chat failed: {str(e)}")

    assistant_msg = Message(conversation_id=conv.id, role="assistant", content=response_text)
    db.add(assistant_msg)
    await db.commit()
    return {"conversation_id": conv.id, "role_preset": conv.role_preset, "message": response_text}


@router.post("/assistant/graphrag/stream")
async def chat_graphrag_stream(
    req: GraphRAGChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(chat_user),
):
    """SSE streaming version of GraphRAG chat."""
    user_id = get_user_id(current_user)
    await load_user_api_keys(user_id, db, current_user is not None)
    if current_user is not None:
        await choose_request_model(req, user_id, db)

    conv = await _get_or_create_conversation(db, ChatRequest(
        message=req.message, conversation_id=req.conversation_id,
        role_preset=req.role_preset, model=req.model,
    ), user_id)
    user_msg = Message(conversation_id=conv.id, role="user", content=req.message)
    db.add(user_msg)
    await db.flush()
    await db.commit()
    history = await _get_history(db, conv.id)

    async def event_generator():
        from app.database import async_session
        import asyncio as _asyncio
        full = ""
        last_hb = _asyncio.get_event_loop().time()
        try:
            async with closing_stream(rag_assistant.chat_stream_with_rag(req.message, req.role_preset, history, req.model, req.top_k, user_id=user_id, mode='graphrag')) as owned_stream:
                async for chunk in owned_stream:
                    full += chunk
                    now = _asyncio.get_event_loop().time()
                    if now - last_hb > 15:
                        yield f": heartbeat\n\n"
                        last_hb = now
                    yield f"data: {json.dumps({'token': chunk, 'done': False})}\n\n"
            async with async_session() as save_db:
                save_db.add(Message(conversation_id=conv.id, role="assistant", content=full))
                await save_db.commit()
            yield f"data: {json.dumps({'token': '', 'done': True, 'conversation_id': conv.id})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e), 'done': True})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/search/graphrag")
async def search_graphrag(query: str = Query(..., min_length=1, max_length=4000), top_k: int = Query(8, ge=1, le=100)):
    """Search with GraphRAG: returns both raw results and formatted context."""
    query = query.strip()
    if not query:
        raise HTTPException(422, "搜索内容不能为空")
    try:
        result = await rag_assistant.search_graphrag(query, top_k)
    except RetrievalUnavailableError as e:
        raise HTTPException(503, str(e)) from e
    return result


@router.delete("/conversations/{conv_id}")
async def delete_conversation(conv_id: str, db: AsyncSession = Depends(get_db), current_user=Depends(chat_user)):
    """Delete a conversation and all its messages."""
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == get_user_id(current_user)))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    await db.delete(conv)
    await db.commit()
    return {"status": "deleted"}


class RenameConversationRequest(BaseModel):
    title: str


@router.put("/conversations/{conv_id}")
async def rename_conversation(conv_id: str, req: RenameConversationRequest, db: AsyncSession = Depends(get_db), current_user=Depends(chat_user)):
    """Rename a conversation."""
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == get_user_id(current_user)))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    conv.title = req.title.strip()[:200]
    await db.commit()
    return {"status": "updated", "title": conv.title}


@router.get("/graphrag/stats")
async def graphrag_stats():
    """Get knowledge graph index statistics."""
    from app.core.graph_rag import get_graph_stats
    stats = await get_graph_stats()
    return stats


@router.post("/graphrag/rebuild")
async def graphrag_rebuild():
    """Force rebuild the knowledge graph index."""
    from app.core.graph_rag import rebuild_graph
    count = await rebuild_graph()
    return {"status": "ok", "node_count": count}


# -- helpers --

async def _get_or_create_conversation(db: AsyncSession, req: ChatRequest, user_id: str = "local_user") -> Conversation:
    if req.conversation_id:
        result = await db.execute(select(Conversation).where(Conversation.id == req.conversation_id, Conversation.user_id == user_id))
        conv = result.scalar_one_or_none()
        if not conv:
            raise HTTPException(404, "Conversation not found")
        return conv
    conv = Conversation(title=req.message[:30], role_preset=req.role_preset, user_id=user_id)
    db.add(conv)
    await db.flush()
    return conv


async def _get_history(db: AsyncSession, conv_id: str) -> list[dict]:
    result = await db.execute(
        select(Message).where(Message.conversation_id == conv_id).order_by(Message.created_at)
    )
    return [{"role": m.role, "content": m.content} for m in result.scalars().all()]
