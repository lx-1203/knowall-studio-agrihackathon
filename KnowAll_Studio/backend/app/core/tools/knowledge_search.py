"""Knowledge search tool — searches the local knowledge base via RAG pipeline."""
from __future__ import annotations

import logging
import asyncio
from typing import Any

from app.core.tools.base import Tool, ToolResult, tool_registry

logger = logging.getLogger("knowall.tools.knowledge_search")


class KnowledgeSearchTool(Tool):
    """Search the local knowledge base for relevant document content."""

    @property
    def name(self) -> str:
        return "knowledge_search"

    @property
    def description(self) -> str:
        return "在本地知识库中搜索相关文档内容。使用RAG检索找到最相关的知识片段。"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索查询",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回结果数量",
                    "default": 5,
                },
                "mode": {
                    "type": "string",
                    "description": "检索模式: rag/graphrag/llamaindex",
                    "default": "rag",
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs) -> ToolResult:
        query = kwargs.get("query", "")
        top_k = kwargs.get("top_k", 5)
        mode = kwargs.get("mode", "rag")

        if not query:
            return ToolResult(success=False, error="query is required")

        try:
            if mode == "graphrag":
                from app.core.graph_rag import graph_rag_query
                context, raw = await graph_rag_query(query=query, top_k_vector=top_k, strict=True)
                results = [
                    {
                        "title": r.title,
                        "text": r.text[:500],
                        "source": r.source,
                        "score": r.relevance_score,
                        "id": r.id or r.node_id or "",
                        "node_id": r.node_id,
                        "metadata": dict(r.metadata),
                        "relation_path": r.relation_path,
                    }
                    for r in raw
                ]
            elif mode in ("llamaindex", "hybrid"):
                from app.core.rag_pipeline import get_pipeline_by_name
                pipeline = get_pipeline_by_name("llamaindex")
                search_result = await pipeline.search(query, top_k=top_k)
                results = [
                    {
                        "title": r.title,
                        "text": r.text[:500],
                        "source": r.source,
                        "score": r.relevance_score,
                        "id": r.id,
                        "metadata": r.metadata,
                    }
                    for r in search_result.results
                ]
                context = search_result.context
            else:
                from app.core.rag import search as rag_search
                raw = await asyncio.to_thread(rag_search, query, n_results=top_k, strict=True)
                results = [
                    {
                        "title": (r.get("metadata", {}) or {}).get("doc_id", ""),
                        "text": r.get("text", "")[:500],
                        "source": "vector",
                        "score": 1.0 - r.get("distance", 0),
                        "id": r.get("id", ""),
                        "metadata": r.get("metadata") or {},
                    }
                    for r in raw
                ]
                context = "\n---\n".join(r.get("text", "") for r in raw)

            return ToolResult(
                success=True,
                output={"results": results, "context": context},
                metadata={"query": query, "mode": mode, "result_count": len(results)},
            )

        except Exception as e:
            logger.error("Knowledge search failed: %s", e)
            return ToolResult(success=False, error=str(e))


# Auto-register
tool_registry.register(KnowledgeSearchTool())
