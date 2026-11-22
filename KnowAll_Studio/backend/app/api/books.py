"""Book API — endpoints for creating and managing learning books."""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.core.auth import get_user_id, load_user_api_keys
from app.core.generation_access import generation_user
from app.core.documents import assert_documents_access
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("knowall.api.books")
router = APIRouter(prefix="/api/v1/books", tags=["books"])


class BookCreateRequest(BaseModel):
    title: str = Field(..., min_length=2, description="教材标题")
    description: str = Field(default="", description="教材描述")
    source_doc_ids: list[str] = Field(default_factory=list, description="源文档ID列表")
    source_kb: str = Field(default="", description="知识库名称")

    @field_validator("title", mode="before")
    @classmethod
    def trim_title(cls, value):
        return value.strip() if isinstance(value, str) else value


@router.post("/create")
async def create_book(req: BookCreateRequest, db: AsyncSession = Depends(get_db), current_user=Depends(generation_user)):
    """Create and compile a new learning book."""
    from app.core.book.compiler import book_compiler

    user_id = get_user_id(current_user)
    await assert_documents_access(db, req.source_doc_ids[:10], current_user.id if current_user else None)
    await load_user_api_keys(user_id, db, current_user is not None)
    book = await book_compiler.create_book(
        title=req.title,
        source_doc_ids=req.source_doc_ids,
        source_kb=req.source_kb,
        description=req.description,
        user_id=user_id,
    )

    return {
        "id": book.id,
        "title": book.title,
        "status": book.status.value,
        "progress": book.progress,
    }


@router.get("")
async def list_books(current_user=Depends(generation_user)):
    """List all books."""
    from app.core.book.compiler import book_compiler
    return {"books": book_compiler.list_books(get_user_id(current_user))}


@router.get("/{book_id}")
async def get_book(book_id: str, current_user=Depends(generation_user)):
    """Get book details and content."""
    from app.core.book.compiler import book_compiler

    book = book_compiler.get_book(book_id, get_user_id(current_user))
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    chapters = []
    for ch in book.chapters:
        sections = []
        for sec in ch.sections:
            blocks = [
                {
                    "id": b.id,
                    "type": b.block_type.value,
                    "content": b.content,
                    "order_index": b.order_index,
                }
                for b in sec.blocks
            ]
            sections.append({
                "id": sec.id,
                "title": sec.title,
                "description": sec.description,
                "blocks": blocks,
                "order_index": sec.order_index,
            })
        chapters.append({
            "id": ch.id,
            "title": ch.title,
            "description": ch.description,
            "sections": sections,
            "order_index": ch.order_index,
        })

    return {
        "id": book.id,
        "title": book.title,
        "subtitle": book.subtitle,
        "description": book.description,
        "status": book.status.value,
        "progress": book.progress,
        "chapters": chapters,
        "error": book.error,
    }


@router.get("/{book_id}/export")
async def export_book(book_id: str, current_user=Depends(generation_user)):
    """Export book as Markdown."""
    from app.core.book.compiler import book_compiler

    book = book_compiler.get_book(book_id, get_user_id(current_user))
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    from app.core.book.models import BookStatus
    if book.status != BookStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="教材尚未完成，无法导出")
    markdown = book_compiler.export_markdown(book)

    from fastapi.responses import Response
    from urllib.parse import quote
    return Response(
        content=markdown,
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename=book-{book.id}.md; filename*=UTF-8''{quote(book.title + '.md', safe='')}"},
    )


@router.delete("/{book_id}")
async def delete_book(book_id: str, current_user=Depends(generation_user)):
    """Delete a book."""
    from app.core.book.compiler import book_compiler
    if not book_compiler.delete_book(book_id, get_user_id(current_user)):
        raise HTTPException(status_code=404, detail="Book not found")
    return {"status": "ok"}
