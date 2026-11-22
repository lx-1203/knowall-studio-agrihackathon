"""ChromaDB RAG Pipeline — wraps existing ChromaDB vector search as a pipeline provider."""
from __future__ import annotations

import logging
import asyncio
from typing import Any

from app.core.rag_pipeline.base import BaseRAGPipeline, RAGResult, SearchResult

logger = logging.getLogger("knowall.rag.chromadb")


class ChromaDBPipeline(BaseRAGPipeline):
    """ChromaDB-backed vector retrieval. Wraps the existing rag.py functions."""

    @property
    def name(self) -> str:
        return "chromadb"

    async def search(
        self, query: str, kb_name: str = "default", top_k: int = 5, **kwargs
    ) -> SearchResult:
        from app.core.rag import search as chroma_search

        raw = await asyncio.to_thread(chroma_search, query, n_results=top_k, strict=True)
        results = [
            RAGResult(
                id=r.get("id", ""),
                text=r.get("text", ""),
                source="vector",
                title=(r.get("metadata", {}) or {}).get("doc_id", "文档片段"),
                relevance_score=round(1.0 - r.get("distance", 0), 4),
                metadata=r.get("metadata", {}),
            )
            for r in raw
        ]
        context = "\n\n---\n\n".join(r.text for r in results) if results else ""

        return SearchResult(
            query=query,
            results=results,
            context=context,
            mode="rag",
            stats={"provider": "chromadb", "result_count": len(results)},
        )

    async def initialize(self, kb_name: str, file_paths: list[str], **kwargs) -> bool:
        """Index document chunks (delegate to existing index_chunks)."""
        from app.core.rag import index_chunks
        chunks = kwargs.get("chunks", [])
        if not chunks:
            logger.warning("ChromaDBPipeline.initialize called without chunks")
            return False
        count = index_chunks(chunks)
        return count > 0

    async def add_documents(self, kb_name: str, file_paths: list[str], **kwargs) -> bool:
        return await self.initialize(kb_name, file_paths, **kwargs)

    async def delete(self, kb_name: str, doc_id: str | None = None, **kwargs) -> bool:
        from app.core.rag import delete_vectors_by_doc_id, delete_collection
        if doc_id:
            delete_vectors_by_doc_id(doc_id)
        else:
            delete_collection()
        return True

    async def get_stats(self, kb_name: str = "default") -> dict[str, Any]:
        from app.core.rag import get_index_stats
        return get_index_stats()
