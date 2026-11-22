"""Book Compiler — 5-stage pipeline for generating structured learning books.

Pipeline stages:
  0. Ideation: generate a book proposal from source materials
  1. Spine: create chapter/section structure
  2. Page Planning: plan block types for each section
  3. Block Compilation: generate content for each block
  4. Assembly: combine into final book
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

from app.core.book.models import (
    Book, BookChapter, BookSection, BookBlock,
    BlockType, BookStatus, BookProposal,
)
from app.core.book.block_generators import (
    generate_text_block, generate_quiz_block,
    generate_callout_block, generate_retrieval_practice,
)

logger = logging.getLogger("knowall.book.compiler")


class BookCompiler:
    """Compiles structured learning books from knowledge bases."""

    def __init__(self):
        self._books: dict[str, Book] = {}
        self._compilations: dict[str, asyncio.Task] = {}
        self._closing = False

    async def create_book(
        self,
        title: str,
        source_doc_ids: list[str] | None = None,
        source_kb: str = "",
        description: str = "",
        user_id: str = "local_user",
    ) -> Book:
        """Create a new book and start compilation."""
        if self._closing:
            raise RuntimeError("Book service is shutting down")
        book_id = str(uuid.uuid4())[:8]
        book = Book(
            id=book_id,
            title=title,
            description=description,
            source_doc_ids=source_doc_ids or [],
            source_kb=source_kb,
            status=BookStatus.PLANNING,
            user_id=user_id,
        )
        self._books[book_id] = book

        # Start compilation in background
        job = asyncio.create_task(self._compile(book))
        self._compilations[book_id] = job
        job.add_done_callback(lambda finished: self._finish(book, finished))

        return book

    def _finish(self, book: Book, job: asyncio.Task) -> None:
        if self._compilations.get(book.id) is job:
            self._compilations.pop(book.id, None)
        if job.cancelled() and book.status not in (BookStatus.COMPLETED, BookStatus.ERROR):
            book.status = BookStatus.ERROR
            book.error = "Book compilation cancelled"

    async def close(self) -> None:
        """Cancel all compilations, including deleted books still cleaning up."""
        self._closing = True
        jobs = list(self._compilations.values())
        for job in jobs:
            if not job.cancelling():
                job.cancel()
        cleanup = asyncio.gather(*jobs, return_exceptions=True)
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            await cleanup
            raise

    def get_book(self, book_id: str, user_id: str | None = None) -> Book | None:
        book = self._books.get(book_id)
        return book if book and (user_id is None or book.user_id == user_id) else None

    def list_books(self, user_id: str | None = None) -> list[dict]:
        return [
            {
                "id": b.id,
                "title": b.title,
                "subtitle": b.subtitle,
                "status": b.status.value,
                "progress": b.progress,
                "chapter_count": len(b.chapters),
                "source_kb": b.source_kb,
            }
            for b in sorted(self._books.values(), key=lambda x: x.id, reverse=True)
            if user_id is None or b.user_id == user_id
        ]

    def delete_book(self, book_id: str, user_id: str | None = None) -> bool:
        if self.get_book(book_id, user_id) is None:
            return False
        book = self._books.pop(book_id, None)
        job = self._compilations.get(book_id)
        if job is not None and not job.cancelling():
            job.cancel()
        return book is not None

    def export_markdown(self, book: Book) -> str:
        """Export book as Markdown."""
        parts = [f"# {book.title}\n"]
        if book.subtitle:
            parts.append(f"*{book.subtitle}*\n")
        if book.description:
            parts.append(f"{book.description}\n")

        for i, chapter in enumerate(book.chapters, 1):
            parts.append(f"\n## 第{i}章 {chapter.title}\n")
            if chapter.description:
                parts.append(f"*{chapter.description}*\n")

            for section in chapter.sections:
                parts.append(f"\n### {section.title}\n")
                for block in section.blocks:
                    if block.block_type == BlockType.TEXT:
                        parts.append(block.content)
                    elif block.block_type == BlockType.QUIZ:
                        parts.append(self._format_quiz_md(block))
                    elif block.block_type == BlockType.CALLOUT:
                        parts.append(self._format_callout_md(block))
                    elif block.block_type == BlockType.FLASH_CARDS:
                        parts.append(self._format_flashcards_md(block))
                    elif block.block_type == BlockType.RETRIEVAL_PRACTICE:
                        parts.append(f"\n> 📝 {block.content}\n")
                    parts.append("")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Compilation pipeline
    # ------------------------------------------------------------------

    async def _compile(self, book: Book):
        """Run the full 5-stage compilation pipeline."""
        from app.core.api_scheduler.client import _current_user_id
        token = _current_user_id.set(book.user_id)
        try:
            # Gather source context
            context = await self._gather_context(book)

            # Stage 0-1: Ideation + Spine
            book.status = BookStatus.PLANNING
            book.progress = 0.05
            proposal = await self._ideate(book, context)

            # Build chapter/section structure
            for ch_idx, ch_data in enumerate(proposal.chapters):
                chapter = BookChapter(
                    id=f"{book.id}_ch{ch_idx}",
                    title=ch_data.get("title", f"第{ch_idx+1}章"),
                    description=ch_data.get("description", ""),
                    order_index=ch_idx,
                )
                for sec_idx, sec_data in enumerate(ch_data.get("sections", [])):
                    section = BookSection(
                        id=f"{chapter.id}_s{sec_idx}",
                        title=sec_data.get("title", f"第{sec_idx+1}节"),
                        description=sec_data.get("description", ""),
                        order_index=sec_idx,
                    )
                    chapter.sections.append(section)
                book.chapters.append(chapter)

            book.subtitle = proposal.subtitle
            book.progress = 0.2

            # Stage 3: Block Compilation
            book.status = BookStatus.COMPILING
            total_sections = sum(len(ch.sections) for ch in book.chapters)
            if not total_sections:
                raise ValueError("教材提纲没有可编译的章节内容")
            done = 0

            for chapter in book.chapters:
                for section in chapter.sections:
                    section_context = f"{context[:2000]}\n\n章节：{chapter.title} - {section.title}"

                    # Generate text content
                    text_content = await generate_text_block(
                        section.title, section_context, chapter.title
                    )
                    section.blocks.append(BookBlock(
                        id=f"{section.id}_text",
                        block_type=BlockType.TEXT,
                        content=text_content,
                        order_index=0,
                    ))

                    # Generate quiz for this section
                    quiz_data = await generate_quiz_block(
                        section.title, section_context, chapter.title
                    )
                    section.blocks.append(BookBlock(
                        id=f"{section.id}_quiz",
                        block_type=BlockType.QUIZ,
                        content=json.dumps(quiz_data, ensure_ascii=False),
                        order_index=1,
                    ))

                    # Generate callout (key point)
                    callout = await generate_callout_block(section.title, section_context)
                    section.blocks.append(BookBlock(
                        id=f"{section.id}_callout",
                        block_type=BlockType.CALLOUT,
                        content=json.dumps(callout, ensure_ascii=False),
                        order_index=2,
                    ))

                    # Retrieval practice
                    rp = await generate_retrieval_practice(section.title, section_context)
                    section.blocks.append(BookBlock(
                        id=f"{section.id}_rp",
                        block_type=BlockType.RETRIEVAL_PRACTICE,
                        content=rp,
                        order_index=3,
                    ))

                    done += 1
                    book.progress = 0.2 + 0.7 * done / total_sections

            # Stage 4: Assembly complete
            book.status = BookStatus.COMPLETED
            book.progress = 1.0

            logger.info("Book compiled: %s — %d chapters, %d total sections",
                        book.title, len(book.chapters), total_sections)

        except asyncio.CancelledError:
            book.status = BookStatus.ERROR
            book.error = "Book compilation cancelled"
            raise
        except Exception as e:
            book.status = BookStatus.ERROR
            book.error = str(e)
            logger.error("Book compilation failed: %s — %s", book.title, e)
        finally:
            _current_user_id.reset(token)

    async def _gather_context(self, book: Book) -> str:
        """Gather context from source documents/knowledge base."""
        context_parts = []

        if book.source_doc_ids:
            from sqlalchemy import select
            from app.database import async_session
            from app.models import DocumentChunk
            from app.core.documents import assert_documents_access

            async with async_session() as db:
                documents = await assert_documents_access(db, book.source_doc_ids[:10], book.user_id)
                result = await db.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.doc_id.in_([doc.id for doc in documents]))
                    .limit(20)
                )
                for chunk in result.scalars().all():
                    context_parts.append(chunk.text_content[:500])

        if not context_parts:
            # Fallback: use knowledge search
            from app.core.tools import execute_tool
            result = await execute_tool("knowledge_search", query=book.title, top_k=10)
            if result.success and result.output:
                for r in result.output.get("results", []):
                    context_parts.append(r.get("text", ""))

        return "\n\n".join(context_parts[:30]) or book.title

    async def _ideate(self, book: Book, context: str) -> BookProposal:
        """Stage 0-1: Generate a book proposal."""
        from app.core.api_scheduler import api_client

        prompt = f"""你是一位优秀的教材策划专家。请为以下主题设计一本学习教材的结构。

主题：{book.title}
描述：{book.description}

参考材料摘要：
{context[:3000]}

请设计 4-6 章的结构，每章包含 2-4 个节。以JSON格式返回：
{{
  "subtitle": "副标题",
  "chapters": [
    {{
      "title": "章节标题",
      "description": "章节简介",
      "sections": [
        {{"title": "节标题", "description": "节内容简介"}}
      ]
    }}
  ]
}}

只返回JSON。"""

        from app.core.api_scheduler.client import GenerationConfig, AllModelsFailedError
        invalid_response = False
        def validate(content):
            nonlocal invalid_response
            valid = self._parse_proposal(content) is not None
            invalid_response |= not valid
            return valid
        try:
            result = await api_client.chat(prompt, task_type="book_gen",
                config=GenerationConfig(response_validator=validate))
        except AllModelsFailedError:
            if not invalid_response:
                raise
            result = ""
        try:
            data = self._parse_proposal(result)
            if data is None:
                raise ValueError("invalid book proposal")
            return BookProposal(
                title=book.title,
                subtitle=data.get("subtitle", ""),
                chapters=data["chapters"],
            )
        except Exception:
            # Fallback: single chapter
            return BookProposal(
                title=book.title,
                chapters=[{"title": book.title, "sections": [{"title": "概述"}]}],
            )

    @staticmethod
    def _parse_proposal(content: str) -> dict | None:
        """Check the compiler's structure before consuming or caching it."""
        try:
            import re
            text = content.strip()
            if text.startswith("```"):
                match = re.search(r'\{.*\}', text, re.DOTALL)
                if match:
                    text = match.group(0)
            data = json.loads(text)
            if not isinstance(data, dict) or not isinstance(data.get("subtitle", ""), str):
                return None
            chapters = data.get("chapters")
            if not isinstance(chapters, list) or not chapters:
                return None
            for chapter in chapters:
                if not isinstance(chapter, dict) or any(
                    not isinstance(chapter.get(field, ""), str) for field in ("title", "description")
                ):
                    return None
                sections = chapter.get("sections", [])
                if not isinstance(sections, list) or any(
                    not isinstance(section, dict) or any(
                        not isinstance(section.get(field, ""), str) for field in ("title", "description")
                    ) for section in sections
                ):
                    return None
            return data
        except (ValueError, TypeError, AttributeError):
            return None

    @staticmethod
    def _format_quiz_md(block: BookBlock) -> str:
        try:
            data = json.loads(block.content)
            parts = ["**📝 章节测试**\n"]
            for i, q in enumerate(data.get("questions", []), 1):
                parts.append(f"{i}. {q.get('question', '')}")
                for opt in q.get("options", []):
                    parts.append(f"   - {opt.get('label', '')}. {opt.get('text', '')}")
                parts.append(f"   *答案: {q.get('answer', '')}*")
                if q.get("explanation"):
                    parts.append(f"   *解析: {q['explanation']}*")
                parts.append("")
            return "\n".join(parts)
        except Exception:
            return block.content

    @staticmethod
    def _format_callout_md(block: BookBlock) -> str:
        try:
            data = json.loads(block.content)
            icon = {"key_point": "💡", "warning": "⚠️", "tip": "💡", "example": "📌"}.get(data.get("type", ""), "💡")
            return f"\n> {icon} **{data.get('label', '提示')}**: {data.get('content', '')}\n"
        except Exception:
            return f"\n> 💡 {block.content}\n"

    @staticmethod
    def _format_flashcards_md(block: BookBlock) -> str:
        try:
            cards = json.loads(block.content)
            if isinstance(cards, dict):
                cards = cards.get("cards", [])
            parts = ["**🃏 闪卡复习**\n"]
            for card in cards:
                parts.append(f"- **Q**: {card.get('front', '')}")
                parts.append(f"  **A**: {card.get('back', '')}")
            return "\n".join(parts)
        except Exception:
            return ""


# Module singleton
book_compiler = BookCompiler()
