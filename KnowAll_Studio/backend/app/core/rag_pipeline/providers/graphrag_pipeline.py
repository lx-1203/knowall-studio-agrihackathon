"""GraphRAG Pipeline — wraps existing hybrid vector + knowledge graph retrieval."""
from __future__ import annotations

import logging
from typing import Any

from app.core.rag_pipeline.base import BaseRAGPipeline, RAGResult, SearchResult

logger = logging.getLogger("knowall.rag.graphrag")


class GraphRAGPipeline(BaseRAGPipeline):
    """Hybrid vector + knowledge graph retrieval via beam-search BFS."""

    @property
    def name(self) -> str:
        return "graphrag"

    async def search(
        self, query: str, kb_name: str = "default", top_k: int = 8, **kwargs
    ) -> SearchResult:
        from app.core.graph_rag import graph_rag_query, get_graph_stats

        max_hops = kwargs.get("max_hops", 2)
        max_context_chars = kwargs.get("max_context_chars", 8000)

        context, raw_results = await graph_rag_query(
            query=query,
            top_k_vector=top_k,
            max_hops=max_hops,
            max_context_chars=max_context_chars,
            strict=kwargs.get("strict", True),
        )

        results = [
            RAGResult(
                id=r.id or r.node_id or "",
                text=r.text,
                source=r.source,
                title=r.title,
                relevance_score=r.relevance_score,
                relation_path=r.relation_path,
                metadata=dict(r.metadata),
            )
            for r in raw_results
        ]

        stats = await get_graph_stats()

        return SearchResult(
            query=query,
            results=results,
            context=context,
            mode="graphrag",
            stats={
                "provider": "graphrag",
                "result_count": len(results),
                "graph_stats": stats,
            },
        )

    async def delete(self, kb_name: str, doc_id: str | None = None, **kwargs) -> bool:
        from app.core.graph_rag import rebuild_graph
        await rebuild_graph(force=True)
        return True

    async def get_stats(self, kb_name: str = "default") -> dict[str, Any]:
        from app.core.graph_rag import get_graph_stats
        return await get_graph_stats()
