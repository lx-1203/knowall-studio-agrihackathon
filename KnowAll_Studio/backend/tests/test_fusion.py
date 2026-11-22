"""fusion.py 的单测：全部是纯函数，不需要 DB / 网络 / 模型。

覆盖 WeKnora 移植里的关键语义：RRF 归一化、[0,1] 值域、BM25 缩放（以及已归一化
分数不被二次缩放）、MMR 的多样性选择、以及 NaN / Inf 的边界行为。
"""
from __future__ import annotations

import math

import pytest

from app.core.rag_pipeline.fusion import (
    IndexWithScore,
    RetrievalConfig,
    RetrieveResult,
    best_ranks,
    classify_retrieval_results,
    composite_score,
    deduplicate_by_score,
    effective_rrf_k,
    effective_rrf_weights,
    flatten_lists,
    fuse_or_deduplicate,
    fuse_with_rrf,
    rescale_unbounded_scores,
    select_mmr,
    tokenize_simple,
)


def _c(chunk_id: str, score: float, content: str = "") -> IndexWithScore:
    return IndexWithScore(chunk_id=chunk_id, score=score, content=content)


def test_hybrid_chunk_hit_by_both_retrievers_ranks_first_with_score_one():
    vector_lists = [[_c("a", 0.9), _c("b", 0.8)]]
    keyword_lists = [[_c("a", 12.5), _c("c", 4.0)]]

    fused = fuse_or_deduplicate(vector_lists, keyword_lists)

    assert [r.chunk_id for r in fused] == ["a", "b", "c"]
    # 两路都排第一 → RRF 恰好等于归一化分母
    assert fused[0].score == pytest.approx(1.0)
    # 只有向量命中且排第一 → 恰好是向量权重 0.7
    assert fused[1].score == pytest.approx(0.7 * 61 / 62)
    # 只有关键词命中且排第一 → 恰好是关键词权重 0.3
    assert fused[2].score == pytest.approx(0.3 * 61 / 62)


def test_rrf_normalization_holds_for_any_k():
    vector_lists = [[_c("a", 0.9)]]
    keyword_lists = [[_c("a", 2.0)]]

    for rrf_k in (20, 60, 100):
        fused = fuse_with_rrf(vector_lists, keyword_lists, RetrievalConfig(rrf_k=rrf_k))
        assert fused[0].score == pytest.approx(1.0)

    # cfg 缺省（None）走默认 k=60 与 0.7/0.3
    assert fuse_with_rrf(vector_lists, keyword_lists).pop().score == pytest.approx(1.0)


def test_every_path_keeps_scores_in_unit_range_and_sorted_desc():
    vector_lists = [[_c("v1", 0.87), _c("v2", 0.42)]]
    keyword_lists = [[_c("k1", 25.0), _c("k2", 11.0)]]

    hybrid = fuse_or_deduplicate(vector_lists, keyword_lists)
    vector_only = fuse_or_deduplicate([[_c("v1", 0.87), _c("v2", 0.42)]], [])
    keyword_only = fuse_or_deduplicate([], [[_c("k1", 25.0), _c("k2", 11.0)]])

    for path in (hybrid, vector_only, keyword_only):
        scores = [r.score for r in path]
        assert all(0.0 <= s <= 1.0 for s in scores)
        assert scores == sorted(scores, reverse=True)

    # 单路向量：引擎给的余弦相似度原样保留（FAQ 阈值依赖它）
    assert [r.score for r in vector_only] == pytest.approx([0.87, 0.42])
    # 单路关键词：除以本条列表的最高分
    assert [r.score for r in keyword_only] == pytest.approx([1.0, 11.0 / 25.0])


def test_bm25_rescaled_but_normalized_scores_left_alone():
    bm25 = [_c("a", 30.0), _c("b", 15.0), _c("c", 7.5)]
    rescale_unbounded_scores(bm25)
    assert [r.score for r in bm25] == pytest.approx([1.0, 0.5, 0.25])

    already_normalized = [_c("x", 0.9), _c("y", 0.5), _c("z", 0.0)]
    rescale_unbounded_scores(already_normalized)
    assert [r.score for r in already_normalized] == pytest.approx([0.9, 0.5, 0.0])

    # 最大值正好是 1 也不动：Qdrant / Doris 会把关键词命中直接标成 1.0
    stamped_one = [_c("q", 1.0), _c("d", 0.4)]
    rescale_unbounded_scores(stamped_one)
    assert [r.score for r in stamped_one] == pytest.approx([1.0, 0.4])

    # 非正分数与 BM25 一起缩放时落到 0
    signed = [_c("a", 4.0), _c("b", 2.0), _c("c", -3.0), _c("d", 0.0)]
    rescale_unbounded_scores(signed)
    assert [r.score for r in signed] == pytest.approx([1.0, 0.5, 0.0, 0.0])

    # 只有关键词的融合路径必须先 rescale，再去重排序
    fused = fuse_or_deduplicate([], [[_c("a", 30.0), _c("b", 15.0), _c("c", 7.5)]])
    assert [r.chunk_id for r in fused] == ["a", "b", "c"]
    assert [r.score for r in fused] == pytest.approx([1.0, 0.5, 0.25])


def test_mmr_prefers_the_more_diverse_of_two_tied_candidates():
    results = [
        _c("a", 1.0, "refund policy refund within seven days of purchase"),
        _c("b", 0.8, "refund policy refund within seven days"),
        _c("c", 0.8, "shipping address change instructions"),
    ]

    # b 与 a 高度重叠、被冗余项扣分，c 同分但更远 → 选 c
    assert select_mmr(results, 2) == [0, 2]
    # 纯相关性（lambda=1）时同分平手取更靠前的
    assert select_mmr(results, 2, lambda_=1.0) == [0, 1]
    assert select_mmr(results, 3) == [0, 2, 1]
    # 非正 k / 空输入
    assert select_mmr(results, 0) == []
    assert select_mmr([], 3) == []


def test_non_finite_scores_do_not_raise():
    nan, inf = float("nan"), float("inf")

    keyword_lists = [
        [_c("n", nan), _c("p", inf), _c("big", 10.0), _c("neg", -inf), _c("zero", 0.0)]
    ]
    fused = fuse_or_deduplicate([], keyword_lists)
    scores = {r.chunk_id: r.score for r in fused}
    assert scores["p"] == 1.0
    assert scores["big"] == 1.0
    assert scores["n"] == 0.0
    assert scores["neg"] == 0.0
    assert scores["zero"] == 0.0
    assert all(math.isfinite(r.score) for r in fused)

    # 没有大于 1 的有限分数时整条列表原样返回（Go 的 maxScore <= 1 提前 return）
    all_non_finite = [_c("i", inf), _c("n", nan)]
    rescale_unbounded_scores(all_non_finite)
    assert math.isinf(all_non_finite[0].score)
    assert math.isnan(all_non_finite[1].score)

    # 向量独有路径不缩放、也不炸
    vector_only = fuse_or_deduplicate([[_c("v", nan), _c("w", 0.5)]], [])
    assert len(vector_only) == 2

    # 混检路径的分数完全由名次决定，畸形的输入分不会漏进结果
    hybrid = fuse_or_deduplicate([[_c("a", inf), _c("b", nan)]], [[_c("b", 7.0)]])
    assert all(0.0 <= r.score <= 1.0 and math.isfinite(r.score) for r in hybrid)


def test_classify_and_flatten_keep_one_list_per_retrieve_result():
    retrieve_results = [
        RetrieveResult(retriever_type="vector", results=[_c("a", 0.9)]),
        RetrieveResult(retriever_type="vector", results=[_c("b", 0.5)]),
        RetrieveResult(retriever_type="keyword", results=[_c("a", 3.0)]),
        RetrieveResult(retriever_type="keyword", results=[]),
        RetrieveResult(
            retriever_type="keyword",
            retriever_engine_type="qdrant",
            results=[_c("c", 1.0)],
        ),
    ]

    vector_lists, keyword_lists = classify_retrieval_results(retrieve_results)

    # 空列表被跳过；每条 RetrieveResult 保持成独立子列表
    assert [len(one) for one in vector_lists] == [1, 1]
    assert [len(one) for one in keyword_lists] == [1, 1]
    assert [r.chunk_id for r in flatten_lists(vector_lists)] == ["a", "b"]
    assert [r.chunk_id for r in flatten_lists(keyword_lists)] == ["a", "c"]


def test_deduplicate_keeps_the_highest_score_per_chunk():
    best = _c("a", 0.9)
    deduped = deduplicate_by_score([_c("a", 0.4), _c("b", 0.5), best])

    assert [r.chunk_id for r in deduped] == ["a", "b"]
    assert [r.score for r in deduped] == pytest.approx([0.9, 0.5])
    assert deduped[0] is best


def test_best_ranks_takes_the_best_rank_across_lists():
    lists = [[_c("a", 0.9), _c("b", 0.8)], [_c("b", 5.0), _c("a", 1.0)]]
    assert best_ranks(lists) == {"a": 1, "b": 1}

    # 同一列表内重复出现时按最高分那条计名次
    assert best_ranks([[_c("a", 0.3), _c("a", 0.9)]]) == {"a": 1}


def test_config_defaults_and_effective_fallbacks_match_go():
    cfg = RetrievalConfig()
    assert (cfg.embedding_top_k, cfg.rerank_top_k) == (50, 10)
    assert (cfg.vector_threshold, cfg.keyword_threshold) == (0.15, 0.3)
    assert cfg.rerank_threshold == pytest.approx(0.2)
    assert (cfg.rrf_k, cfg.rrf_vector_weight, cfg.rrf_keyword_weight) == (60, 0.7, 0.3)

    assert effective_rrf_k(None) == 60
    assert effective_rrf_weights(None) == (0.7, 0.3)
    assert effective_rrf_k(RetrievalConfig(rrf_k=0)) == 60
    assert RetrievalConfig(rrf_k=20).get_effective_rrf_k() == 20
    # 只配了向量权重，关键词权重补默认值而不是被归一化掉
    assert RetrievalConfig(
        rrf_vector_weight=0.9, rrf_keyword_weight=0.0
    ).get_effective_rrf_weights() == (0.9, 0.3)


def test_composite_score_weights_and_clamp():
    candidate = _c("a", 0.5)
    # 0.6*model + 0.3*base + 0.1*source
    assert composite_score(candidate, 0.9, 0.5) == pytest.approx(0.79)
    assert composite_score(candidate, 0.9) == pytest.approx(0.79)

    graph = IndexWithScore(chunk_id="g", score=0.0, match_type="graph")
    assert composite_score(graph, 0.8) == pytest.approx(0.82)

    web = IndexWithScore(chunk_id="w", score=0.5, knowledge_source="web_search")
    assert composite_score(web, 0.5) == pytest.approx(0.545)

    assert composite_score(candidate, 2.0, 1.0) == 1.0
    assert composite_score(candidate, -1.0, 0.0) == 0.0
    assert math.isnan(composite_score(candidate, float("nan"), 0.5))


def test_tokenize_simple_uses_jieba_word_boundaries():
    """jieba 在位时中文按词典切词，而不是退化成相邻二字。

    断言「大学生」和「自然语言」：相邻二字切法只会给出「大学」「学生」，「自然」
    「语言」不会连成「自然语言」（那是搜索引擎模式额外吐出的子串）。这两个 token
    同时出现，就说明真的走了 jieba。项目本来就把 jieba 列为依赖，所以这里不 skip，
    而是直接要求它可用 —— jieba 缺席是部署问题，不该被测试掩盖。
    """
    tokens = tokenize_simple("大学生学习系统里的自然语言处理")
    assert "大学生" in tokens
    assert "自然语言" in tokens
    assert "学习" in tokens


def test_tokenize_simple_drops_single_chars_and_punctuation():
    # 单字和纯标点都被过滤，剩下的集合为空 —— 否则 MMR 的 Jaccard 会被
    # 「的」「，」这类高频噪声主导，任意两段中文都显得很相似。
    assert tokenize_simple("的 了 吧 ，，") == set()
