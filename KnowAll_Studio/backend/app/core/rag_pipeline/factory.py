"""RAG Pipeline factory — dispatches to the configured provider."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.rag_pipeline.base import BaseRAGPipeline

if TYPE_CHECKING:
    pass

logger = logging.getLogger("knowall.rag_pipeline")

# Provider registry: maps name → import path
_PROVIDERS: dict[str, str] = {
    "chromadb": "app.core.rag_pipeline.providers.chromadb_pipeline.ChromaDBPipeline",
    "graphrag": "app.core.rag_pipeline.providers.graphrag_pipeline.GraphRAGPipeline",
    "llamaindex": "app.core.rag_pipeline.providers.llamaindex_pipeline.LlamaIndexPipeline",
}

# Singleton cache
_instances: dict[str, BaseRAGPipeline] = {}


def _import_class(dotted_path: str):
    """Import a class from a dotted path string."""
    module_path, class_name = dotted_path.rsplit(".", 1)
    import importlib
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def get_pipeline_by_name(provider: str) -> BaseRAGPipeline:
    """Get a pipeline instance by provider name."""
    if provider in _instances:
        return _instances[provider]

    if provider not in _PROVIDERS:
        raise ValueError(f"Unknown RAG provider: {provider}. Available: {list(_PROVIDERS.keys())}")

    cls = _import_class(_PROVIDERS[provider])
    instance = cls()
    _instances[provider] = instance
    logger.info("RAG pipeline initialized: %s", provider)
    return instance


def get_pipeline() -> BaseRAGPipeline:
    """Get the default pipeline based on settings."""
    from app.config import settings
    provider = getattr(settings, "rag_provider", "chromadb")
    return get_pipeline_by_name(provider)


def register_provider(name: str, dotted_path: str):
    """Register a custom RAG provider at runtime."""
    _PROVIDERS[name] = dotted_path
    logger.info("Registered RAG provider: %s → %s", name, dotted_path)


def list_providers() -> list[str]:
    """List all available provider names."""
    return list(_PROVIDERS.keys())
