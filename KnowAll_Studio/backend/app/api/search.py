"""RAG search API routes"""
import asyncio
import logging
from typing import Literal
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field, field_validator
from app.database import get_db
from app.core.rag import search, retrieve_context, get_index_stats, index_chunks, RetrievalUnavailableError
from app.models import Document, DocumentChunk
from app.core.auth import get_optional_user
from app.core.document_search import keyword_search, label_sources

router = APIRouter(prefix="/api/v1/search", tags=["search"])
logger = logging.getLogger("knowall.search")


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=100)
    mode: Literal['keyword', 'semantic', 'ai'] = 'semantic'

    @field_validator('query', mode='before')
    @classmethod
    def trim_query(cls, value):
        return value.strip() if isinstance(value, str) else value


@router.post("/")
async def search_chunks(req: SearchRequest, db: AsyncSession = Depends(get_db),
                        current_user=Depends(get_optional_user)):
    """Search for relevant document chunks using vector similarity."""
    if req.mode == 'ai':
        from app.core.unified_search import unified_search
        return await unified_search(db, req.query, req.top_k, getattr(current_user, 'id', None))
    if req.mode == 'keyword':
        user_id = getattr(current_user, 'id', None)
        try:
            results = await keyword_search(db, req.query, req.top_k, user_id)
        except Exception as e:
            logger.exception('Document keyword search failed')
            raise HTTPException(503, '资料搜索暂不可用，请稍后重试') from e
        return {"query": req.query, "results": results, "count": len(results)}
    # ChromaDB queries are sync + CPU/disk-bound; run off the event loop.
    try:
        results = await asyncio.to_thread(search, req.query, n_results=req.top_k, strict=True)
        results = await label_sources(db, results)
    except RetrievalUnavailableError as e:
        raise HTTPException(503, str(e)) from e
    return {"query": req.query, "results": results, "count": len(results)}


@router.post("/rag")
async def retrieve_and_generate(req: SearchRequest, db: AsyncSession = Depends(get_db)):
    """Retrieve relevant context for RAG (to be combined with LLM call)."""
    try:
        result = await asyncio.to_thread(retrieve_context, req.query, req.top_k, strict=True)
        result['sources'] = await label_sources(db, result['sources'])
    except RetrievalUnavailableError as e:
        raise HTTPException(503, str(e)) from e
    return {"query": req.query, **result, "has_results": bool(result['context'])}


@router.post("/index")
async def index_document_chunks(doc_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    """Index all chunks of a document into vector store."""
    from sqlalchemy import select
    doc = await db.get(Document, doc_id, populate_existing=True)
    if doc is None:
        raise HTTPException(404, "Document not found")
    if (doc.metadata_ or {}).get('knowledge_owner_id'):
        raise HTTPException(403, '私有资料不能加入全局检索；请通过知识网络或资料学习使用')
    result = await db.execute(
        select(DocumentChunk)
        .where(DocumentChunk.doc_id == doc_id)
        .order_by(DocumentChunk.chunk_index)
    )
    chunks = result.scalars().all()

    if not chunks:
        raise HTTPException(404, "No chunks found for this document")

    chunk_dicts = [
        {
            "id": c.id,
            "text": c.text_content,
            "metadata": {"doc_id": c.doc_id, "chunk_index": c.chunk_index, "page_range": c.page_range, "token_count": c.token_count},
        }
        for c in chunks
    ]

    count = await asyncio.to_thread(index_chunks, chunk_dicts)

    if count != len(chunks):
        raise HTTPException(503, "文档索引失败，请检查检索服务后重试")

    # Update vector_id in database for tracking/deletion
    for c in chunks:
        c.vector_id = c.id  # ChromaDB uses the chunk UUID as the vector ID
    try:
        await db.commit()
    except Exception as e:
        logger.exception("Failed to commit document index markers")
        try:
            await db.rollback()
        except Exception:
            logger.exception("Failed to roll back document index markers")
        # Vectors may already exist. Retrying the same IDs uses upsert.
        raise HTTPException(503, "文档索引状态保存失败，请稍后重试") from e

    return {"indexed": count, "doc_id": doc_id}


@router.get("/stats")
async def search_stats():
    """Get vector index statistics."""
    return await asyncio.to_thread(get_index_stats)
