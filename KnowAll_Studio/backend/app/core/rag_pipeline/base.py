"""RAG Pipeline protocol and shared data types."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("knowall.rag_pipeline")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class RAGResult:
    """A single retrieval result."""
    id: str
    text: str
    source: str  # "vector" | "graph" | "bm25" | "node_semantic"
    title: str = ""
    relevance_score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    relation_path: str = ""


@dataclass
class SearchResult:
    """Search response container."""
    query: str
    results: list[RAGResult]
    context: str  # formatted context string for LLM injection
    mode: str = "rag"  # "rag" | "graphrag" | "hybrid"
    stats: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pipeline protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class RAGPipeline(Protocol):
    """Protocol that all RAG providers must implement."""

    @property
    def name(self) -> str:
        """Provider identifier (e.g. 'llamaindex', 'chromadb', 'graphrag')."""
        ...

    async def initialize(self, kb_name: str, file_paths: list[str], **kwargs) -> bool:
        """Build or rebuild the index for a knowledge base from given files."""
        ...

    async def add_documents(self, kb_name: str, file_paths: list[str], **kwargs) -> bool:
        """Incrementally add documents to an existing index."""
        ...

    async def search(self, query: str, kb_name: str = "default", top_k: int = 5, **kwargs) -> SearchResult:
        """Retrieve relevant results for a query."""
        ...

    async def delete(self, kb_name: str, doc_id: str | None = None, **kwargs) -> bool:
        """Delete a knowledge base or a specific document from it."""
        ...

    async def get_stats(self, kb_name: str = "default") -> dict[str, Any]:
        """Return indexing statistics."""
        ...


class BaseRAGPipeline(ABC):
    """Abstract base class with common helpers. Concrete providers extend this."""

    def __init__(self):
        self._logger = logging.getLogger(f"knowall.rag.{self.name}")

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def search(self, query: str, kb_name: str = "default", top_k: int = 5, **kwargs) -> SearchResult:
        ...

    async def initialize(self, kb_name: str, file_paths: list[str], **kwargs) -> bool:
        self._logger.warning("initialize() not implemented for %s", self.name)
        return False

    async def add_documents(self, kb_name: str, file_paths: list[str], **kwargs) -> bool:
        self._logger.warning("add_documents() not implemented for %s", self.name)
        return False

    async def delete(self, kb_name: str, doc_id: str | None = None, **kwargs) -> bool:
        self._logger.warning("delete() not implemented for %s", self.name)
        return False

    async def get_stats(self, kb_name: str = "default") -> dict[str, Any]:
        return {"provider": self.name, "status": "ok"}
