"""混合检索单测：BM25 一路 + 加权 RRF 融合 + 可回退开关。

测试的核心设计：**故意让向量一路答错**（把正确片段排到最后），再断言融合后它
排到第一。如果向量本来就答对，这个测试就证明不了融合有用 —— 那才是摆设。
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.core import rag


class _FakeCollection:
    """手写的测试替身，只实现 rag.py 真正用到的那几个方法。

    为什么不用真的 ChromaDB：真库的默认 embedding function 会去下载 ONNX 模型，
    测试离线跑不动；而且这里要验的是"我的 BM25 和融合接线对不对"，不是 ChromaDB
    的行为。真实 get() 的返回结构另有 TestRealChromadbShape 专门覆盖。
    """

    def __init__(self, name: str, rows: dict, vector_order: list | None = None):
        self.name = name
        self._rows = dict(rows)  # id -> (text, metadata)
        self._vector_order = list(vector_order) if vector_order else None

    def count(self) -> int:
        return len(self._rows)

    def add(self, doc_id: str, text: str, metadata: dict | None = None) -> None:
        self._rows[doc_id] = (text, metadata or {})

    def get(self, include=None):
        ids = list(self._rows)
        return {
            "ids": ids,
            "documents": [self._rows[i][0] for i in ids],
            "metadatas": [self._rows[i][1] for i in ids],
        }

    def query(self, query_texts=None, n_results=5):
        order = self._vector_order or list(self._rows)
        order = [i for i in order if i in self._rows][:n_results]
        return {
            "ids": [order],
            "documents": [[self._rows[i][0] for i in order]],
            "metadatas": [[self._rows[i][1] for i in order]],
            "distances": [[0.1 * (k + 1) for k in range(len(order))]],
        }


# 语料：a 是关键词强相关但被向量排到最后的那条
CORPUS = {
    "a": ("TCP 三次握手会建立连接", {"doc_id": "d1"}),
    "b": ("网络协议概述与分层模型", {"doc_id": "d2"}),
    "c": ("数据库事务的隔离级别", {"doc_id": "d3"}),
}
QUERY = "TCP 三次握手"


@pytest.fixture(autouse=True)
def _clear_bm25_cache(monkeypatch):
    """每个用例前清空索引缓存，避免用例之间互相污染。"""
    rag._bm25_cache.clear()
    # These retrieval unit fixtures use synthetic public IDs, not production SQL.
    # Real authoritative ownership is covered by search citation/scope fixtures.
    async def public_ids(ids):
        return ids
    monkeypatch.setattr(rag, '_public_document_ids', public_ids)
    yield
    rag._bm25_cache.clear()


@pytest.fixture
def fake_collection():
    return _FakeCollection("document_knowledge", CORPUS, vector_order=["b", "c", "a"])


@pytest.fixture
def patched_collection(monkeypatch, fake_collection):
    monkeypatch.setattr(rag, "get_or_create_collection", lambda *a, **k: fake_collection)
    return fake_collection


class TestBm25Index:
    """BM25 打分本身的行为。"""

    def test_exact_term_doc_ranks_first(self):
        index = rag._BM25Index(list(CORPUS), [v[0] for v in CORPUS.values()], [v[1] for v in CORPUS.values()])
        ranked = index.rank(QUERY, 5)
        assert ranked, "应该至少命中一条"
        assert index.ids[ranked[0][0]] == "a"

    def test_unmatched_docs_are_excluded_not_ranked_zero(self):
        index = rag._BM25Index(["a", "b"], ["TCP 三次握手建立连接", "数据库事务隔离级别"], [{}, {}])
        ranked = index.rank(QUERY, 5)
        # 一个词都没命中的 chunk 不该占候选位
        assert [index.ids[i] for i, _ in ranked] == ["a"]

    def test_shorter_document_wins_on_length_normalization(self):
        long_text = "TCP 握手 " + "无关内容 " * 40
        index = rag._BM25Index(
            ["short", "long"], ["TCP 三次握手建立连接", long_text], [{}, {}]
        )
        ranked = index.rank("三次握手", 5)
        assert index.ids[ranked[0][0]] == "short", "长度归一化应该让短文档占优"

    def test_repeated_term_has_higher_term_frequency(self):
        index = rag._BM25Index(
            ["once", "repeated"], ["apple", "apple apple apple apple apple"], [{}, {}]
        )

        ranked = index.rank("apple", 2)

        assert index._postings[1]["apple"] == 5
        assert index.ids[ranked[0][0]] == "repeated"
        assert ranked[0][1] > ranked[1][1]

    def test_rare_term_outweighs_common_term(self):
        """idf 要让罕见词说话。

        语料必须够大才有意义：只有两篇文档时"常见词"根本统计不出常见，idf 几乎一样。
        这里 5 篇里 4 篇含"协议"、只有 1 篇含"三次握手"，两者 idf 才拉得开。
        """
        ids = ["c1", "c2", "c3", "c4", "rare"]
        docs = ["协议 概述", "协议 分层", "协议 模型", "协议 分层", "三次握手"]
        index = rag._BM25Index(ids, docs, [{}, {}, {}, {}, {}])
        scores = dict(index.rank("协议 三次握手", 5))

        assert "rare" in [index.ids[i] for i in scores]
        rare_score = scores[[i for i in scores if index.ids[i] == "rare"][0]]
        best_common = max(
            score for i, score in scores.items() if index.ids[i].startswith("c")
        )
        assert rare_score > best_common, (
            f"罕见词 {rare_score:.3f} 应该明显压过常见词 {best_common:.3f}"
        )

    def test_empty_corpus_and_empty_query_are_safe(self):
        assert rag._BM25Index([], [], []).rank(QUERY, 5) == []
        index = rag._BM25Index(["a"], ["TCP 三次握手"], [{}])
        assert index.rank("", 5) == []
        assert index.rank(QUERY, 0) == []

    def test_ranking_is_capped_at_top_n(self):
        index = rag._BM25Index(
            ["a", "b", "c"],
            ["三次握手", "三次握手", "三次握手"],
            [{}, {}, {}],
        )
        assert len(index.rank("三次握手", 2)) == 2


class TestBm25Cache:
    """索引缓存：必须复用，也必须能感知语料变化。"""

    def test_index_is_reused_when_count_unchanged(self, patched_collection):
        first = rag._bm25_index(patched_collection)
        second = rag._bm25_index(patched_collection)
        assert first is second, "数量没变就该复用同一个索引对象"

    def test_index_rebuilds_when_corpus_grows(self, patched_collection):
        first = rag._bm25_index(patched_collection)
        assert len(first) == 3
        patched_collection.add("d", "三次握手还会释放连接", {"doc_id": "d4"})
        second = rag._bm25_index(patched_collection)
        assert second is not first
        assert len(second) == 4
        assert "d" in [second.ids[i] for i, _ in second.rank("释放连接", 5)]

    def test_same_count_replacement_does_not_return_deleted_chunk(self, patched_collection):
        rag.bm25_search(QUERY)
        count = patched_collection.count()
        del patched_collection._rows["a"]
        patched_collection.add("d", "TCP 三次握手的新版本", {"doc_id": "d4"})
        assert patched_collection.count() == count

        hits = rag.bm25_search(QUERY)
        hybrid = rag.hybrid_search(QUERY, n_results=3)

        assert "d" in [hit["id"] for hit in hits]
        assert "a" not in [hit["id"] for hit in hits]
        assert "a" not in [hit["id"] for hit in hybrid]

    def test_index_write_invalidates_same_id_cached_content(self, patched_collection, monkeypatch):
        rag.bm25_search(QUERY)

        def replace(*, ids, documents, metadatas):
            for chunk_id, document, metadata in zip(ids, documents, metadatas):
                patched_collection._rows[chunk_id] = (document, metadata)

        monkeypatch.setattr(patched_collection, "upsert", replace, raising=False)
        count = rag.index_chunks(
            [{"id": "a", "text": "revised chemistry only", "metadata": {"doc_id": "d1"}}]
        )

        assert count == 1
        assert rag.bm25_search("revised chemistry")[0]["text"] == "revised chemistry only"
        assert "a" not in [hit["id"] for hit in rag.bm25_search(QUERY)]

    def test_same_id_external_update_is_refreshed_after_cache_expiry(
        self, patched_collection, monkeypatch
    ):
        rag.bm25_search(QUERY)
        patched_collection._rows["a"] = ("revised chemistry only", {"doc_id": "d1"})
        monkeypatch.setattr(rag, "_BM25_CACHE_MAX_AGE_SECONDS", 0.0, raising=False)

        assert rag.bm25_search("revised chemistry")[0]["text"] == "revised chemistry only"

    def test_delete_invalidates_cached_index(self, patched_collection, monkeypatch):
        rag.bm25_search(QUERY)

        def delete(*, where):
            patched_collection._rows = {
                key: value
                for key, value in patched_collection._rows.items()
                if value[1].get("doc_id") != where["doc_id"]
            }

        monkeypatch.setattr(patched_collection, "delete", delete, raising=False)
        rag.delete_vectors_by_doc_id("d1")
        assert "document_knowledge" not in rag._bm25_cache
        patched_collection.add("d", "TCP 三次握手的新版本", {"doc_id": "d4"})

        assert patched_collection.count() == len(CORPUS)
        assert "d" in [hit["id"] for hit in rag.bm25_search(QUERY)]
        assert "a" not in [hit["id"] for hit in rag.bm25_search(QUERY)]

    def test_collection_recreation_invalidates_same_ids_and_count(
        self, patched_collection, monkeypatch
    ):
        rag.bm25_search(QUERY)
        replacement = _FakeCollection(
            "document_knowledge",
            {**CORPUS, "a": ("revised chemistry only", {"doc_id": "d1"})},
        )
        active = [patched_collection]

        class Client:
            def delete_collection(self, name):
                assert name == "document_knowledge"
                active[0] = replacement

        monkeypatch.setattr(rag, "get_chroma_client", lambda: Client())
        monkeypatch.setattr(rag, "get_or_create_collection", lambda *a, **k: active[0])

        rag.delete_collection()

        assert rag.bm25_search("revised chemistry")[0]["text"] == "revised chemistry only"

    def test_missing_collection_returns_none(self):
        assert rag._bm25_index(None) is None


class TestHybridSearch:
    """融合接线：两路都命中的片段要被顶到最前。"""

    def test_vector_alone_ranks_the_right_answer_last(self, patched_collection):
        # 先证明前提成立：向量一路把 a 排最后。前提不成立的话下面的断言毫无意义。
        assert [r["id"] for r in rag.search(QUERY, n_results=3)] == ["b", "c", "a"]

    def test_keyword_channel_finds_the_right_answer(self, patched_collection):
        assert [r["id"] for r in rag.bm25_search(QUERY, n_results=3)][0] == "a"

    def test_hybrid_promotes_the_double_hit_to_first(self, patched_collection):
        hits = rag.hybrid_search(QUERY, n_results=3)
        assert hits[0]["id"] == "a", [h["id"] for h in hits]
        # 分数必须归一到 [0,1]，否则下游按分数比较会错乱
        assert all(0.0 <= h["score"] <= 1.0 for h in hits), [h["score"] for h in hits]

    def test_deeper_pool_is_requested(self, patched_collection):
        """候选池要比最终条数深，否则名次没有区分度，融合等于没做。"""
        seen = {}
        original = rag.search

        def spy(query, collection_name="document_knowledge", n_results=5):
            seen["n_results"] = n_results
            return original(query, collection_name, n_results=n_results)

        rag.search = spy
        try:
            rag.hybrid_search(QUERY, n_results=3)
        finally:
            rag.search = original
        assert seen["n_results"] == max(3 * settings.hybrid_pool_factor, settings.hybrid_min_pool)

    def test_degrades_to_vector_order_when_no_keyword_hit(self, patched_collection):
        # "机器学习"在语料里一个词都不出现 → 关键词一路为空
        assert rag.bm25_search("机器学习", n_results=5) == []
        hybrid = [r["id"] for r in rag.hybrid_search("机器学习", n_results=3)]
        plain = [r["id"] for r in rag.search("机器学习", n_results=3)]
        assert hybrid == plain, "关键词一路为空时必须完全退化成改动前的行为"

    def test_deep_burial_in_vector_list_still_loses(self, monkeypatch):
        """记录一个容易被误读的语义：加权 RRF 只提升名次，不覆盖向量权重。

        为什么专门写这一条：看到"融合后正确片段不是第 1 名"，很容易当成 bug 去调
        权重。实际上向量权重 0.7 压过关键词 0.3，一段在向量一路被埋到第 4 名的内容
        融合后是第 2 名 —— 这是 RRF 的定义决定的。上游在融合之后还有一道 rerank
        （本项目没移植），把第 2 名提成第 1 名是那一步的职责，不是融合的。

        这个语料是照真实场景复刻的：b 因为正文里出现了 "TCP/IP" 也进了关键词榜
        （排在 a 后面），于是它在两路都靠前，融合后总分反超只靠关键词一路的 a。
        真实 ChromaDB 上跑出来正是 ['b', 'a', 'd']。
        """
        corpus = dict(CORPUS)
        corpus["b"] = ("网络协议概述与 TCP/IP 分层模型", {"doc_id": "d2"})
        corpus["d"] = ("进程调度算法与时间片轮转", {"doc_id": "d4"})
        buried = _FakeCollection(
            "document_knowledge", corpus, vector_order=["d", "b", "c", "a"]
        )
        monkeypatch.setattr(rag, "get_or_create_collection", lambda *a, **k: buried)

        fused = [h["id"] for h in rag.hybrid_search(QUERY, n_results=3)]
        # 关键是"名次被提升"而不是"必须第一"
        assert fused.index("a") < 3, fused
        # b 在向量一路第 2、关键词一路第 2，两路都靠前，所以它压过 a
        assert fused[0] == "b", fused

    def test_empty_collection_returns_empty(self, monkeypatch):
        empty = _FakeCollection("empty_kb", {}, vector_order=[])
        monkeypatch.setattr(rag, "get_or_create_collection", lambda *a, **k: empty)
        assert rag.hybrid_search(QUERY, n_results=3) == []

    def test_mmr_only_kicks_in_when_enabled(self, monkeypatch, patched_collection):
        monkeypatch.setattr(settings, "hybrid_mmr_enabled", True)
        with_mmr = [r["id"] for r in rag.hybrid_search(QUERY, n_results=2)]
        monkeypatch.setattr(settings, "hybrid_mmr_enabled", False)
        without = [r["id"] for r in rag.hybrid_search(QUERY, n_results=2)]
        # 两种模式都必须返回合法结果；MMR 只应改变取舍，不应丢结果
        assert len(with_mmr) == 2 and len(without) == 2
        assert set(with_mmr) <= {"a", "b", "c"}
        assert set(without) <= {"a", "b", "c"}


class TestHybridFlag:
    """回退开关：关掉就是改动前的行为，这是出问题时的救命开关。"""

    def test_flag_on_uses_hybrid(self, monkeypatch, patched_collection):
        monkeypatch.setattr(settings, "hybrid_retrieval_enabled", True)
        context = rag.rag_query(QUERY, top_k=3)
        assert context.split("\n\n---\n\n")[0] == CORPUS["a"][0]

    def test_flag_off_uses_plain_vector(self, monkeypatch, patched_collection):
        monkeypatch.setattr(settings, "hybrid_retrieval_enabled", False)
        context = rag.rag_query(QUERY, top_k=3)
        assert context.split("\n\n---\n\n")[0] == CORPUS["b"][0]

    def test_empty_results_give_empty_context(self, monkeypatch):
        empty = _FakeCollection("empty_kb2", {}, vector_order=[])
        monkeypatch.setattr(rag, "get_or_create_collection", lambda *a, **k: empty)
        monkeypatch.setattr(settings, "hybrid_retrieval_enabled", True)
        assert rag.rag_query(QUERY, top_k=3) == ""


class TestRealChromadbShape:
    """用真的 ChromaDB 验一次语料读取。

    替身能证明我的接线对，但证明不了"真库的 count()/get() 返回结构就是这样"。
    这条用例用显式向量（不触发 embedding function，因此不下载模型）。
    """

    def test_bm25_index_reads_real_collection(self):
        import chromadb

        client = chromadb.EphemeralClient()
        collection = client.get_or_create_collection("bm25_shape_test")
        collection.add(
            ids=["x1", "x2"],
            documents=["TCP 三次握手会建立连接", "数据库事务的隔离级别"],
            metadatas=[{"doc_id": "d1"}, {"doc_id": "d2"}],
            embeddings=[[1.0, 0.0], [0.0, 1.0]],
        )

        index = rag._bm25_index(collection)
        assert index is not None
        assert len(index) == 2

        ranked = index.rank("三次握手", 5)
        assert ranked, "真实 ChromaDB 语料里应该能命中"
        assert index.ids[ranked[0][0]] == "x1"
        assert index.metas[ranked[0][0]] == {"doc_id": "d1"}

    def test_real_collection_triggers_rebuild_on_change(self):
        import chromadb

        client = chromadb.EphemeralClient()
        collection = client.get_or_create_collection("bm25_shape_rebuild_test")
        collection.add(ids=["y1"], documents=["三次握手"], embeddings=[[1.0, 0.0]])
        assert len(rag._bm25_index(collection)) == 1

        collection.add(ids=["y2"], documents=["滑动窗口"], embeddings=[[0.5, 0.5]])
        assert len(rag._bm25_index(collection)) == 2

    def test_real_collection_detects_same_count_id_replacement(self):
        import chromadb

        client = chromadb.EphemeralClient()
        collection = client.get_or_create_collection("bm25_shape_replace_test")
        collection.add(ids=["old"], documents=["outdated biology"], embeddings=[[1.0, 0.0]])
        assert rag._bm25_index(collection).ids == ["old"]

        collection.delete(ids=["old"])
        collection.add(ids=["new"], documents=["new chemistry"], embeddings=[[0.0, 1.0]])

        assert rag._bm25_index(collection).ids == ["new"]
