"""Local RAG vector search layer - ChromaDB-backed, fully offline"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import math
import time
from app.config import settings

logger = logging.getLogger("knowall")
_client = None


class RetrievalUnavailableError(RuntimeError):
    """Safe public failure; internal storage details stay in server logs."""

    def __init__(self):
        super().__init__("检索服务暂不可用，请稍后重试")


async def _public_document_ids(document_ids: set[str]) -> set[str]:
    """Check authoritative ownership; old vector metadata may lack an owner.

    The isolated engine is never shared across loops or pooled with requests.
    Unknown/deleted IDs are excluded. No ownership decision is cached.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from sqlalchemy.pool import NullPool
    from app.models import Document

    connect_args = {'timeout': 3} if settings.database_url.startswith('sqlite') else {'connect_timeout': 3}
    engine = create_async_engine(settings.database_url, poolclass=NullPool, connect_args=connect_args)
    try:
        async with async_sessionmaker(engine)() as db:
            async with asyncio.timeout(5):
                rows = (await db.execute(select(Document.id, Document.metadata_)
                                         .where(Document.id.in_(document_ids)))).all()
            return {doc_id for doc_id, metadata in rows if not (metadata or {}).get('knowledge_owner_id')}
    finally:
        await engine.dispose()


def _global_public_hits(hits: list[dict]) -> list[dict]:
    """Private uploads cannot be global citations, even for their owner."""
    candidates = [hit for hit in hits if not (hit.get('metadata') or {}).get('knowledge_owner_id')]
    document_ids = {str(hit['metadata']['doc_id']) for hit in candidates
                    if (hit.get('metadata') or {}).get('doc_id')}
    if not document_ids:
        return candidates
    def verify():
        return asyncio.run(_public_document_ids(document_ids))
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        public_ids = verify()
    else:
        # Legacy sync callers also run inside async generation handlers.
        with ThreadPoolExecutor(max_workers=1) as worker:
            public_ids = worker.submit(verify).result()
    return [hit for hit in candidates if not (hit.get('metadata') or {}).get('doc_id')
            or str(hit['metadata']['doc_id']) in public_ids]


def _get_chromadb():
    """Lazy import chromadb to avoid hard dependency on startup."""
    try:
        from chromadb import PersistentClient
        from chromadb.config import Settings as ChromaSettings
        return PersistentClient, ChromaSettings
    except ImportError:
        return None, None


def get_chroma_client():
    """Get or create the ChromaDB persistent client."""
    global _client
    if _client is None:
        PersistentClient, ChromaSettings = _get_chromadb()
        if PersistentClient is None:
            logger.warning("ChromaDB not installed. RAG search disabled.")
            return None
        try:
            _client = PersistentClient(
                path=settings.chroma_persist_dir,
                settings=ChromaSettings(anonymized_telemetry=False),
            )
            logger.info("ChromaDB initialized at %s", settings.chroma_persist_dir)
        except Exception as e:
            logger.warning("ChromaDB unavailable: %s. RAG search disabled.", e)
            return None
    return _client


def get_or_create_collection(name: str = "document_knowledge"):
    """Get or create a ChromaDB collection. Returns None if unavailable."""
    client = get_chroma_client()
    if client is None:
        return None
    return client.get_or_create_collection(name=name)


def index_chunks(chunks: list[dict], collection_name: str = "document_knowledge"):
    """Index document chunks into vector store. Returns count or 0."""
    try:
        if len(_global_public_hits(chunks)) != len(chunks):
            logger.warning("Refused private chunks in global vector index")
            return 0
        collection = get_or_create_collection(collection_name)
        if collection is None:
            return 0
        ids = [c["id"] for c in chunks]
        texts = [c["text"] for c in chunks]
        # Optional SQL fields can be None, which Chroma metadata rejects.
        metadatas = [
            {key: value for key, value in (c.get("metadata") or {}).items() if value is not None} or None
            for c in chunks
        ]
        try:
            collection.upsert(ids=ids, documents=texts, metadatas=metadatas)
            # Upsert merges metadata. Explicitly cleared optional fields must
            # also remove their old values, otherwise citations keep old pages.
            cleared = [
                (chunk['id'], {key: None for key, value in (chunk.get('metadata') or {}).items() if value is None})
                for chunk in chunks
            ]
            cleared = [(chunk_id, metadata) for chunk_id, metadata in cleared if metadata]
            if cleared:
                collection.update(ids=[item[0] for item in cleared], metadatas=[item[1] for item in cleared])
        finally:
            _bm25_cache.pop(collection_name, None)
        logger.info("Indexed %d chunks into %s", len(chunks), collection_name)
        return len(chunks)
    except Exception as e:
        logger.error("Failed to index chunks: %s", e)
        return 0


def search(query: str, collection_name: str = "document_knowledge", n_results: int = 5, *, strict: bool = False) -> list[dict]:
    """Search chunks; strict callers distinguish failures from successful empties."""
    try:
        collection = get_or_create_collection(collection_name)
        if collection is None:
            if strict:
                raise RetrievalUnavailableError()
            return []
        results = collection.query(query_texts=[query], n_results=n_results)
        return _global_public_hits([
            {
                "id": id_,
                "text": doc,
                "metadata": meta,
                "distance": round(dist, 4),
            }
            for id_, doc, meta, dist in zip(
                results.get("ids", [[]])[0],
                results.get("documents", [[]])[0],
                results.get("metadatas", [[]])[0],
                results.get("distances", [[]])[0],
            )
        ])
    except Exception as e:
        logger.error("Search failed: %s", e)
        if strict:
            raise RetrievalUnavailableError() from e
        return []


# ---------------------------------------------------------------------------
# 混合检索：向量 + BM25 双路召回，加权 RRF 融合（移植自 WeKnora）
# ---------------------------------------------------------------------------
# BM25 用标准 Okapi 参数。这两个数字是信息检索领域的惯例值，不是调出来的：
# k1 控制词频饱和（一个词出现 10 次不会比出现 3 次相关 3 倍），b 控制长度归一。
BM25_K1 = 1.5
BM25_B = 0.75

# 其他进程若用相同 ID 更新内容，ID 检查无法识别；到期后强制重新读取正文。
_BM25_CACHE_MAX_AGE_SECONDS = 60.0

# {collection_name: {"key": 数量, "ids": ID 集合, "built_at": 单调时钟, "index": _BM25Index}}
_bm25_cache: dict = {}


def _tokenize_for_bm25(text: str) -> list[str]:
    """分词复用融合模块那一套（中文走 jieba，其余按空白切）。

    两路口径必须一致，否则关键词一路的召回质量会莫名其妙地差，还很难查。
    """
    from app.core.rag_pipeline.fusion import tokenize_terms

    return tokenize_terms(text or "")


class _BM25Index:
    """内存版 Okapi BM25 索引，语料直接取自向量库那批 chunk。

    为什么语料取自向量库、而不是从数据库读：两路召回的 id 必须来自同一套命名，
    否则"同一段内容被两路同时命中"这个信号就丢了 —— 而 RRF 的价值恰恰在于奖励
    这种双命中。用向量库自己的 id，两路天然一致。

    为什么不引 rank_bm25 之类的库：BM25 公式本身只有十几行，而多一个依赖就多
    一处只在部署时才炸的坑（这也是移植 WeKnora 时的取舍：它的 BM25 由 Qdrant /
    OpenSearch 等引擎提供，没有可移植的 Python 实现，所以这里按标准公式自己算）。
    """

    __slots__ = ("ids", "docs", "metas", "_postings", "_lengths", "_avg_len", "_n")

    def __init__(self, ids, docs, metas):
        self.ids = list(ids)
        self.docs = list(docs)
        self.metas = list(metas)
        self._postings: list[dict[str, int]] = []
        self._lengths: list[int] = []
        for doc in self.docs:
            tf: dict[str, int] = {}
            for token in _tokenize_for_bm25(doc):
                tf[token] = tf.get(token, 0) + 1
            self._postings.append(tf)
            self._lengths.append(sum(tf.values()))
        self._n = len(self.docs)
        total = sum(self._lengths)
        self._avg_len = (total / self._n) if self._n else 0.0

    def __len__(self) -> int:
        return self._n

    def rank(self, query: str, top_n: int) -> list[tuple[int, float]]:
        """返回 (语料下标, 分数) 降序。零分不返回。

        为什么把零分滤掉：一个词都没匹配上的 chunk 和查询毫无关系，让它占住候选池
        会把真正命中的片段挤出去。
        """
        if not self._n or top_n <= 0 or self._avg_len <= 0:
            return []
        terms = _tokenize_for_bm25(query)
        if not terms:
            return []

        # df 现算：查询词通常只有几个，扫一遍 postings 比常驻维护一张 df 表更省内存
        df = {t: sum(1 for tf in self._postings if t in tf) for t in set(terms)}

        scored: list[tuple[int, float]] = []
        for i, tf in enumerate(self._postings):
            norm = BM25_K1 * (1.0 - BM25_B + BM25_B * self._lengths[i] / self._avg_len)
            score = 0.0
            for t in terms:
                freq = tf.get(t)
                if not freq:
                    continue
                idf = math.log(1.0 + (self._n - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * freq * (BM25_K1 + 1.0) / (freq + norm)
            if score > 0.0:
                scored.append((i, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_n]


def _bm25_index(collection, *, strict: bool = False) -> _BM25Index | None:
    """缓存 BM25 分词结果；每次核对 ID，定期刷新同 ID 的正文更新。"""
    if collection is None:
        if strict:
            raise RetrievalUnavailableError()
        return None
    try:
        count = collection.count()
    except Exception as e:
        logger.warning("BM25: 取 chunk 数失败: %s", e)
        if strict:
            raise RetrievalUnavailableError() from e
        return None

    name = getattr(collection, "name", "document_knowledge") or "document_knowledge"
    # Same name/IDs can belong to a different store or a recreated collection.
    collection_id = getattr(collection, "id", None) or collection
    cached = _bm25_cache.get(name)
    if (
        cached
        and cached.get("collection_id") == collection_id
        and cached.get("key") == count
        and cached.get("index") is not None
        and time.monotonic() - cached.get("built_at", 0) < _BM25_CACHE_MAX_AGE_SECONDS
    ):
        try:
            current_ids = collection.get(include=[]).get("ids")
            if current_ids is not None and frozenset(current_ids) == cached.get("ids"):
                return cached["index"]
        except Exception as e:
            logger.warning("BM25: 核对 chunk ID 失败，重建索引: %s", e)

    try:
        data = collection.get(include=["documents", "metadatas"])
    except Exception as e:
        logger.warning("BM25: 取语料失败: %s", e)
        if strict:
            raise RetrievalUnavailableError() from e
        return None

    index = _BM25Index(
        data.get("ids") or [],
        data.get("documents") or [],
        data.get("metadatas") or [],
    )
    _bm25_cache[name] = {
        "collection_id": collection_id,
        "key": len(index),
        "ids": frozenset(index.ids),
        "built_at": time.monotonic(),
        "index": index,
    }
    logger.info("BM25 索引已重建: %s, %d 条", name, len(index))
    return index


def bm25_search(query: str, collection_name: str = "document_knowledge", n_results: int = 5, *, strict: bool = False) -> list[dict]:
    """关键词一路的召回。返回结构与 search() 对齐，分数换成 BM25 分。"""
    try:
        index = _bm25_index(get_or_create_collection(collection_name), strict=strict)
        if index is None:
            return []
        return _global_public_hits([
            {
                "id": index.ids[i],
                "text": index.docs[i],
                "metadata": index.metas[i] or {},
                "score": round(score, 4),
            }
            for i, score in index.rank(query, n_results)
        ])
    except Exception as e:
        logger.error("Keyword search failed: %s", e)
        if strict:
            raise RetrievalUnavailableError() from e
        return []


def _as_candidate(hit: dict, *, vector: bool):
    """把检索结果转成融合层的候选对象。

    分数方向要统一成"越大越好"：ChromaDB 给的是距离（越小越近），所以取负。
    RRF 本身只看名次，但去重阶段要按分数择优，方向反了会挑出错的那条。
    """
    from app.core.rag_pipeline.fusion import IndexWithScore

    if vector:
        distance = hit.get("distance")
        score = -float(distance) if distance is not None else 0.0
    else:
        score = float(hit.get("score") or 0.0)
    return IndexWithScore(
        chunk_id=str(hit.get("id") or ""),
        score=score,
        metadata=hit.get("metadata") or {},
        content=hit.get("text") or "",
    )


def hybrid_search(query: str, collection_name: str = "document_knowledge", n_results: int = 5, *, strict: bool = False) -> list[dict]:
    """向量 + 关键词双路召回后融合（WeKnora 的默认检索形态）。

    为什么要第二路：纯向量检索对"专有名词、公式、编号"这类字面匹配很弱。学生问
    "TCP 三次握手"，向量模型容易召回一堆泛泛的"网络协议"段落，而关键词一路能
    精确命中；两路都命中的片段会被 RRF 顶到最前。

    候选池比最终条数深（settings.hybrid_pool_factor）：名次是 RRF 唯一的输入，
    只捞 top_k 条的话名次没有区分度，融合等于没做。

    降级是内建行为，不是兜底补丁：关键词一路为空（语料还没索引、或查询词一个都
    没匹配上）时，fuse_or_deduplicate 会退化成"按向量分数去重排序"，与改动前一致。
    """
    from app.core.rag_pipeline.fusion import fuse_or_deduplicate, select_mmr

    pool = max(n_results * settings.hybrid_pool_factor, settings.hybrid_min_pool)
    strict_options = {"strict": True} if strict else {}
    vector_list = [
        _as_candidate(h, vector=True)
        for h in search(query, collection_name, n_results=pool, **strict_options)
    ]
    keyword_list = [
        _as_candidate(h, vector=False)
        for h in bm25_search(query, collection_name, n_results=pool, **strict_options)
    ]
    if not vector_list and not keyword_list:
        return []

    # RRF 参数（k=60、权重 0.7/0.3）用融合模块里的默认值，与 WeKnora 一致；
    # 要调就传 RetrievalConfig，不要在这里写死数字。
    fused = fuse_or_deduplicate([vector_list], [keyword_list] if keyword_list else [])

    if settings.hybrid_mmr_enabled and len(fused) > n_results:
        fused = [fused[i] for i in select_mmr(fused, n_results, settings.hybrid_mmr_lambda)]
    else:
        fused = fused[:n_results]

    return [
        {
            "id": c.chunk_id,
            "text": c.content,
            "metadata": c.metadata,
            "score": round(c.score, 4),
        }
        for c in fused
    ]


def retrieve_context(query: str, top_k: int = 3, *, strict: bool = False) -> dict:
    """Retrieve relevant context for a question. Returns concatenated text
    that can be passed to an LLM for answer generation.

    默认走混合检索（向量 + BM25 → 加权 RRF）。把 settings.hybrid_retrieval_enabled
    置为 False 就回到改动前的纯向量行为 —— 排序出问题时一行配置即可回退。
    """
    strict_options = {"strict": True} if strict else {}
    if settings.hybrid_retrieval_enabled:
        results = hybrid_search(query, n_results=top_k, **strict_options)
    else:
        results = search(query, n_results=top_k, **strict_options)
    sources = [dict(result, source_id=index) for index, result in enumerate(results, 1)]
    return {"context": "\n\n---\n\n".join(result["text"] for result in results), "sources": sources}


def rag_query(query: str, top_k: int = 3) -> str:
    """Legacy plain-context contract for existing agent/chat callers."""
    return retrieve_context(query, top_k)["context"]


def delete_vectors_by_doc_id(doc_id: str) -> int:
    """Delete all vectors associated with a document from ChromaDB."""
    try:
        collection = get_or_create_collection()
        if collection is None:
            return 0
        try:
            collection.delete(where={"doc_id": doc_id})
        finally:
            _bm25_cache.pop("document_knowledge", None)
        logger.info("Deleted vectors for doc %s", doc_id)
        return 0
    except Exception as e:
        logger.warning("Failed to delete vectors for doc %s: %s", doc_id, e)
        return 0


def delete_collection(name: str = "document_knowledge"):
    """Delete a collection."""
    try:
        client = get_chroma_client()
        if client is None:
            return
        try:
            client.delete_collection(name)
        finally:
            _bm25_cache.pop(name, None)
        logger.info("Deleted collection: %s", name)
    except Exception as e:
        logger.error("Failed to delete collection: %s", e)


def get_index_stats(collection_name: str = "document_knowledge") -> dict:
    """Get statistics about the indexed collection."""
    try:
        collection = get_or_create_collection(collection_name)
        if collection is None:
            return {"collection": collection_name, "indexed_chunks": 0, "status": "chromadb_unavailable"}
        count = collection.count()
        return {"collection": collection_name, "indexed_chunks": count, "status": "active"}
    except Exception:
        return {"collection": collection_name, "indexed_chunks": 0, "status": "error"}
