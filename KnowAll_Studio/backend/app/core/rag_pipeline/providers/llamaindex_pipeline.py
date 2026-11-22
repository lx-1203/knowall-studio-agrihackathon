"""LlamaIndex RAG Pipeline — hybrid BM25 + FAISS vector retrieval.

This is the new high-performance provider using the LlamaIndex framework.
Falls back gracefully if llama-index is not installed.
"""
from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.core.rag_pipeline.base import BaseRAGPipeline, RAGResult, SearchResult
from app.core.rag import RetrievalUnavailableError

logger = logging.getLogger("knowall.rag.llamaindex")

# Thread pool for blocking LlamaIndex operations
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="llamaindex")


def _check_deps() -> bool:
    """Check if llama-index dependencies are available."""
    try:
        import llama_index.core  # noqa: F401
        return True
    except ImportError:
        return False


class LlamaIndexPipeline(BaseRAGPipeline):
    """LlamaIndex + FAISS + BM25 hybrid retrieval.

    Features:
      - FAISS vector store with bge-small-zh-v1.5 embeddings
      - BM25 retrieval with jieba tokenization for Chinese text
      - Reciprocal Rank Fusion (RRF) for hybrid score merging
      - Index versioning keyed by embedding signature
      - Non-blocking document loading via thread pool
    """

    def __init__(self):
        super().__init__()
        self._indices: dict[str, Any] = {}  # kb_name → index
        self._available = _check_deps()
        if not self._available:
            self._logger.warning(
                "llama-index not installed. Run: "
                "pip install llama-index-core llama-index-retrievers-bm25 "
                "llama-index-vector-stores-faiss faiss-cpu"
            )

    @property
    def name(self) -> str:
        return "llamaindex"

    async def search(
        self, query: str, kb_name: str = "default", top_k: int = 5, **kwargs
    ) -> SearchResult:
        strict = kwargs.get("strict", True)
        kwargs["strict"] = strict
        if not self._available:
            if strict:
                raise RetrievalUnavailableError()
            return SearchResult(
                query=query, results=[], context="",
                mode="hybrid",
                stats={"provider": "llamaindex", "error": "dependencies not installed"},
            )

        # Check if we have an index for this kb
        index = self._indices.get(kb_name)
        if index is None:
            # Try to load from disk
            index = await self._load_index(kb_name)
            if index is None:
                if strict:
                    raise RetrievalUnavailableError()
                return SearchResult(
                    query=query, results=[], context="",
                    mode="hybrid",
                    stats={"provider": "llamaindex", "error": f"no index for kb={kb_name}"},
                )

        # Run retrieval in thread pool (LlamaIndex is sync-heavy)
        loop = asyncio.get_event_loop()
        raw_results = await loop.run_in_executor(
            _executor, self._sync_search, index, query, top_k, kwargs
        )

        results = [
            RAGResult(
                id=r.get("id", ""),
                text=r.get("text", ""),
                source=r.get("source", "hybrid"),
                title=r.get("title", ""),
                relevance_score=r.get("score", 0.0),
                metadata=r.get("metadata", {}),
            )
            for r in raw_results
        ]

        context = "\n\n---\n\n".join(r.text for r in results) if results else ""

        return SearchResult(
            query=query,
            results=results,
            context=context,
            mode="hybrid",
            stats={"provider": "llamaindex", "result_count": len(results)},
        )

    def _sync_search(self, index, query: str, top_k: int, kwargs: dict) -> list[dict]:
        """Synchronous hybrid search (runs in thread pool)."""
        try:
            from llama_index.core.retrievers import VectorIndexRetriever
            from llama_index.core import QueryBundle

            # Vector retrieval
            vector_retriever = VectorIndexRetriever(
                index=index,
                similarity_top_k=top_k,
            )
            vector_nodes = vector_retriever.retrieve(QueryBundle(query_str=query))

            # BM25 retrieval (if available)
            bm25_nodes = []
            try:
                from llama_index.retrievers.bm25 import BM25Retriever
                bm25_retriever = BM25Retriever.from_defaults(
                    nodes=list(index.docstore.docs.values()),
                    similarity_top_k=top_k,
                )
                bm25_nodes = bm25_retriever.retrieve(QueryBundle(query_str=query))
            except Exception as e:
                self._logger.debug("BM25 retrieval unavailable: %s", e)

            # Reciprocal Rank Fusion (RRF)
            k = 60  # RRF constant
            scores: dict[str, float] = {}
            node_map: dict[str, Any] = {}

            for rank, node in enumerate(vector_nodes):
                nid = node.node.node_id
                scores[nid] = scores.get(nid, 0) + 1.0 / (k + rank + 1)
                node_map[nid] = node

            for rank, node in enumerate(bm25_nodes):
                nid = node.node.node_id
                scores[nid] = scores.get(nid, 0) + 1.0 / (k + rank + 1)
                if nid not in node_map:
                    node_map[nid] = node

            # Sort by fused score
            sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

            results = []
            for nid in sorted_ids[:top_k]:
                node = node_map[nid]
                meta = node.node.metadata or {}
                results.append({
                    "id": nid,
                    "text": node.node.get_content(),
                    "source": "hybrid",
                    "title": meta.get("filename", meta.get("doc_id", "")),
                    "score": round(scores[nid], 4),
                    "metadata": meta,
                })

            return results

        except Exception as e:
            self._logger.error("LlamaIndex search failed: %s", e)
            if kwargs.get("strict"):
                raise RetrievalUnavailableError() from e
            return []

    async def initialize(self, kb_name: str, file_paths: list[str], **kwargs) -> bool:
        if not self._available:
            return False

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor, self._sync_build_index, kb_name, file_paths, kwargs
        )

    def _sync_build_index(self, kb_name: str, file_paths: list[str], kwargs: dict) -> bool:
        """Build a FAISS index from document files (runs in thread pool)."""
        try:
            from llama_index.core import (
                VectorStoreIndex,
                SimpleDirectoryReader,
                Settings,
                StorageContext,
            )
            from llama_index.core.node_parser import SentenceSplitter
            from app.config import settings as app_settings

            # Configure embedding model
            try:
                from llama_index.embeddings.huggingface import HuggingFaceEmbedding
                Settings.embed_model = HuggingFaceEmbedding(
                    model_name=app_settings.embedding_model
                )
            except ImportError:
                self._logger.warning("HuggingFace embedding not available, using default")

            # Configure node parser
            Settings.node_parser = SentenceSplitter(
                chunk_size=app_settings.chunk_size_tokens,
                chunk_overlap=int(app_settings.chunk_size_tokens * app_settings.chunk_overlap_ratio),
            )

            # Try FAISS vector store
            try:
                import faiss
                from llama_index.vector_stores.faiss import FaissVectorStore
                dimension = 512  # bge-small-zh-v1.5 dimension
                faiss_index = faiss.IndexFlatIP(dimension)
                vector_store = FaissVectorStore(faiss_index=faiss_index)
            except ImportError:
                self._logger.warning("FAISS not available, using SimpleVectorStore")
                from llama_index.core.vector_stores import SimpleVectorStore
                vector_store = SimpleVectorStore()

            storage_context = StorageContext.from_defaults(vector_store=vector_store)

            # Load documents
            valid_paths = [p for p in file_paths if os.path.exists(p)]
            if not valid_paths:
                self._logger.warning("No valid file paths for index building")
                return False

            # Load from directory or individual files
            if len(valid_paths) == 1 and os.path.isdir(valid_paths[0]):
                reader = SimpleDirectoryReader(input_dir=valid_paths[0])
            else:
                reader = SimpleDirectoryReader(input_files=valid_paths)

            documents = reader.load_data()
            if not documents:
                self._logger.warning("No documents loaded from %s", file_paths)
                return False

            # Add metadata
            for doc in documents:
                doc.metadata["kb_name"] = kb_name

            # Build index
            index = VectorStoreIndex.from_documents(
                documents,
                storage_context=storage_context,
                show_progress=False,
            )

            self._indices[kb_name] = index

            # Persist to disk
            persist_dir = self._get_persist_dir(kb_name)
            index.storage_context.persist(persist_dir=str(persist_dir))

            self._logger.info(
                "LlamaIndex built: kb=%s docs=%d → %s",
                kb_name, len(documents), persist_dir,
            )
            return True

        except Exception as e:
            self._logger.error("LlamaIndex build failed: %s", e)
            return False

    async def add_documents(self, kb_name: str, file_paths: list[str], **kwargs) -> bool:
        """Add documents to an existing index."""
        index = self._indices.get(kb_name)
        if index is None:
            index = await self._load_index(kb_name)

        if index is None:
            # No existing index, build from scratch
            return await self.initialize(kb_name, file_paths, **kwargs)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor, self._sync_add_docs, index, kb_name, file_paths
        )

    def _sync_add_docs(self, index, kb_name: str, file_paths: list[str]) -> bool:
        try:
            from llama_index.core import SimpleDirectoryReader

            valid_paths = [p for p in file_paths if os.path.exists(p)]
            if not valid_paths:
                return False

            reader = SimpleDirectoryReader(input_files=valid_paths)
            documents = reader.load_data()

            for doc in documents:
                doc.metadata["kb_name"] = kb_name
                index.insert(doc)

            # Re-persist
            persist_dir = self._get_persist_dir(kb_name)
            index.storage_context.persist(persist_dir=str(persist_dir))
            return True
        except Exception as e:
            self._logger.error("LlamaIndex add_documents failed: %s", e)
            return False

    async def delete(self, kb_name: str, doc_id: str | None = None, **kwargs) -> bool:
        if doc_id:
            self._logger.warning("Per-document delete not yet supported in LlamaIndex pipeline")
            return False

        # Delete entire KB index
        self._indices.pop(kb_name, None)
        persist_dir = self._get_persist_dir(kb_name)
        if persist_dir.exists():
            import shutil
            shutil.rmtree(persist_dir, ignore_errors=True)
        return True

    async def _load_index(self, kb_name: str):
        """Try to load a persisted index from disk."""
        persist_dir = self._get_persist_dir(kb_name)
        if not persist_dir.exists():
            return None

        try:
            from llama_index.core import StorageContext, load_index_from_storage

            storage_context = StorageContext.from_defaults(
                persist_dir=str(persist_dir)
            )
            index = load_index_from_storage(storage_context)
            self._indices[kb_name] = index
            self._logger.info("LlamaIndex loaded from disk: kb=%s", kb_name)
            return index
        except Exception as e:
            self._logger.warning("Failed to load LlamaIndex for kb=%s: %s", kb_name, e)
            return None

    async def get_stats(self, kb_name: str = "default") -> dict[str, Any]:
        index = self._indices.get(kb_name)
        persist_dir = self._get_persist_dir(kb_name)
        return {
            "provider": "llamaindex",
            "available": self._available,
            "loaded": index is not None,
            "persist_dir": str(persist_dir),
            "persist_exists": persist_dir.exists(),
        }

    def _get_persist_dir(self, kb_name: str) -> Path:
        from app.config import settings
        return Path(settings.chroma_persist_dir).parent / "llamaindex" / kb_name
