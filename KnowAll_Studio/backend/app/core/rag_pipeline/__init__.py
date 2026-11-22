"""RAG Pipeline abstraction layer — provider-agnostic retrieval.

Usage:
    from app.core.rag_pipeline import get_pipeline
    pipeline = get_pipeline()
    results = await pipeline.search("query", kb_name="default")
"""
from app.core.rag_pipeline.base import RAGPipeline, RAGResult, SearchResult
from app.core.rag_pipeline.factory import get_pipeline, get_pipeline_by_name, list_providers

__all__ = [
    "RAGPipeline",
    "RAGResult",
    "SearchResult",
    "get_pipeline",
    "get_pipeline_by_name",
    "list_providers",
]
