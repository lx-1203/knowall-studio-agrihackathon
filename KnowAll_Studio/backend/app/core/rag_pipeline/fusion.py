"""WeKnora 检索结果「融合 / 去重 / MMR」的纯 Python 移植。

对照的 Go 源文件：
- internal/application/service/knowledgebase_search_fusion.go
  （classifyRetrievalResults / flattenLists / fuseOrDeduplicate / sortByScoreDesc /
    deduplicateByScore / rescaleUnboundedScores / fuseWithRRF / bestRanks）
- internal/reranking/rerank.go（CompositeScore 的 0.6/0.3/0.1 权重与 [0,1] clamp）
- internal/reranking/mmr.go（SelectMMR 的 lambda 折中公式）
- internal/types/retrieval_config.go（默认值与 GetEffective* 回退规则）
- internal/searchutil/textutil.go（TokenizeSimple / Jaccard）

全部为同步纯函数：Go 侧的 ctx / logger 只用于打点，这里直接去掉；模块不碰
async、DB、网络，方便单测。
"""
from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from functools import cmp_to_key, lru_cache
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)

__all__ = [
    "IndexWithScore",
    "RetrieveResult",
    "RetrievalConfig",
    "VECTOR_RETRIEVER_TYPE",
    "DEFAULT_RRF_K",
    "DEFAULT_RRF_VECTOR_WEIGHT",
    "DEFAULT_RRF_KEYWORD_WEIGHT",
    "DEFAULT_MMR_LAMBDA",
    "DEFAULT_RERANK_THRESHOLD",
    "DEFAULT_MAX_CANDIDATES",
    "DEFAULT_FALLBACK_MIN_SCORE",
    "COMPOSITE_SCORE_KEY",
    "MATCH_TYPE_GRAPH",
    "classify_retrieval_results",
    "flatten_lists",
    "sort_by_score_desc",
    "deduplicate_by_score",
    "rescale_unbounded_scores",
    "fuse_or_deduplicate",
    "fuse_with_rrf",
    "best_ranks",
    "select_mmr",
    "tokenize_simple",
    "tokenize_terms",
    "jaccard",
    "composite_score",
    "effective_rrf_k",
    "effective_rrf_weights",
    "effective_rerank_threshold",
]

# internal/types/retriever.go：RetrieverType 只有 vector / keyword 两类，
# 非 vector 一律走关键词分支（Go 里就是 else）。
VECTOR_RETRIEVER_TYPE = "vector"

# internal/types/retrieval_config.go 的默认值。
DEFAULT_RETRIEVAL_TOP_K = 50
MAX_REQUESTED_RESULTS = 200
DEFAULT_VECTOR_THRESHOLD = 0.15
DEFAULT_KEYWORD_THRESHOLD = 0.3
DEFAULT_RERANK_TOP_K = 10
DEFAULT_RERANK_THRESHOLD = 0.2
DEFAULT_RRF_K = 60
DEFAULT_RRF_VECTOR_WEIGHT = 0.7
DEFAULT_RRF_KEYWORD_WEIGHT = 0.3

# internal/reranking/mmr.go 与 rerank.go 的常量。
DEFAULT_MMR_LAMBDA = 0.7
DEFAULT_FALLBACK_MIN_SCORE = 0.15
DEFAULT_MAX_CANDIDATES = 200
COMPOSITE_SCORE_KEY = "composite_score"
MATCH_TYPE_GRAPH = "graph"
_SOURCE_WEB_SEARCH = "web_search"


@dataclass
class IndexWithScore:
    """一个「chunk + 分数」候选，对应 Go 的 types.IndexWithScore。

    只保留融合/MMR 真正读到的字段：chunk_id 用于去重与名次映射，score 是被各个
    阶段原地改写的那一个数（Go 里改的也是指针指向的结构体）。content / match_type
    / knowledge_source 供 MMR 的多样性项和 CompositeScore 使用。
    """

    chunk_id: str
    score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    content: str = ""
    chunk_type: str = ""
    knowledge_source: str = ""
    match_type: str = ""


@dataclass
class RetrieveResult:
    """一条检索结果，对应 Go 的 types.RetrieveResult（只保留融合用到的字段）。"""

    retriever_type: str = ""
    retriever_engine_type: str = ""
    results: List[IndexWithScore] = field(default_factory=list)


@dataclass
class RetrievalConfig:
    """租户级检索配置，对应 Go 的 types.RetrievalConfig。

    字段默认值直接取 Go 的 GetEffective* 回退值（50 / 0.15 / 0.3 / 10 / 0.2 /
    60 / 0.7 / 0.3）。Go 的结构体零值是 0、默认值靠 GetEffective* 兜底；这里让
    构造出来的实例就已经是「有效配置」，同时保留同样的回退函数，两者结果一致。
    """

    embedding_top_k: int = DEFAULT_RETRIEVAL_TOP_K
    vector_threshold: float = DEFAULT_VECTOR_THRESHOLD
    keyword_threshold: float = DEFAULT_KEYWORD_THRESHOLD
    rerank_top_k: int = DEFAULT_RERANK_TOP_K
    rerank_threshold: float = DEFAULT_RERANK_THRESHOLD
    rerank_model_id: str = ""
    rrf_k: int = DEFAULT_RRF_K
    rrf_vector_weight: float = DEFAULT_RRF_VECTOR_WEIGHT
    rrf_keyword_weight: float = DEFAULT_RRF_KEYWORD_WEIGHT

    def get_effective_rrf_k(self) -> int:
        return effective_rrf_k(self)

    def get_effective_rrf_weights(self) -> Tuple[float, float]:
        return effective_rrf_weights(self)

    def get_effective_rerank_threshold(self) -> float:
        return effective_rerank_threshold(self)


def effective_rrf_k(cfg: Optional[RetrievalConfig]) -> int:
    """GetEffectiveRRFK：cfg 缺失或 rrf_k 非正时回退到 60。"""
    if cfg is None or cfg.rrf_k <= 0:
        return DEFAULT_RRF_K
    return cfg.rrf_k


def effective_rrf_weights(cfg: Optional[RetrievalConfig]) -> Tuple[float, float]:
    """GetEffectiveRRFWeights：两个权重都没配就给 0.7/0.3；只配了一个则另一个补默认值。

    Go 的注释说两权重「通常应为 1.0，但数学上任意正权重都成立」，所以这里按字段
    逐个回退，而不是把权重归一化掉——归一化会悄悄改掉调用方显式给的比例。
    """
    if cfg is None or (cfg.rrf_vector_weight == 0 and cfg.rrf_keyword_weight == 0):
        return DEFAULT_RRF_VECTOR_WEIGHT, DEFAULT_RRF_KEYWORD_WEIGHT
    vector, keyword = cfg.rrf_vector_weight, cfg.rrf_keyword_weight
    if vector <= 0:
        vector = DEFAULT_RRF_VECTOR_WEIGHT
    if keyword <= 0:
        keyword = DEFAULT_RRF_KEYWORD_WEIGHT
    return vector, keyword


def effective_rerank_threshold(cfg: Optional[RetrievalConfig]) -> float:
    """GetEffectiveRerankThreshold：只有 cfg 缺失才回退 0.2。

    Go 在这里没有非正数判断，因为 0 和负分都是合法的模型分阈值。
    """
    if cfg is None:
        return DEFAULT_RERANK_THRESHOLD
    return cfg.rerank_threshold


def classify_retrieval_results(
    retrieve_results: Sequence[RetrieveResult],
) -> Tuple[List[List[IndexWithScore]], List[List[IndexWithScore]]]:
    """按 retriever 类型把检索结果分成「向量列表组」和「关键词列表组」。

    为什么保持「一条 RetrieveResult 一个子列表」而不在这里拍平：这些列表来自不同
    检索引擎、不同存储分组和不同参数集（文档检索 vs FAQ），并且按 goroutine 完成
    顺序追加，元素位置只有在各自列表内部才有意义（Go 原注释）。空列表被跳过，因为
    它们在后续的名次计算里没有任何贡献。
    """
    vector_lists: List[List[IndexWithScore]] = []
    keyword_lists: List[List[IndexWithScore]] = []
    for retrieve_result in retrieve_results:
        if not retrieve_result.results:
            continue
        if retrieve_result.retriever_type == VECTOR_RETRIEVER_TYPE:
            vector_lists.append(list(retrieve_result.results))
        else:
            keyword_lists.append(list(retrieve_result.results))
    return vector_lists, keyword_lists


def flatten_lists(lists: Sequence[Sequence[IndexWithScore]]) -> List[IndexWithScore]:
    """顺序拼接多个检索列表，不去重、不改分。"""
    out: List[IndexWithScore] = []
    for one_list in lists:
        out.extend(one_list)
    return out


def sort_by_score_desc(a: IndexWithScore, b: IndexWithScore) -> int:
    """降序比较器（与 Go 的 sortByScoreDesc 同形），配合 cmp_to_key 使用。

    Go 的 slices.SortFunc 不稳定，而且下游是从 map 迭代出切片再排序，等分时的顺序
    本来就是任意的；Python 的 sort 稳定，等分时保留插入顺序（向量命中的对象在前、
    关键词独有的在后），结果可复现。
    """
    if a.score > b.score:
        return -1
    if a.score < b.score:
        return 1
    return 0


def deduplicate_by_score(results: Sequence[IndexWithScore]) -> List[IndexWithScore]:
    """按 chunk_id 去重、每个 chunk 保留最高分，然后按分数降序返回。

    同一个 chunk 会从多个引擎或多条列表回来，只有最高分能代表它——FAQ 的阈值判断
    就依赖保留下来的是向量相似度那份分数。等分时保留先出现的那条（Go 用的是严格
    大于比较）。Go 遇到 nil 元素会 panic，这里跳过，因为 flatten 的结果允许带 None。
    """
    chunk_info_map: Dict[str, IndexWithScore] = {}
    for r in results:
        if r is None:
            continue
        existing = chunk_info_map.get(r.chunk_id)
        if existing is None or r.score > existing.score:
            chunk_info_map[r.chunk_id] = r
    deduped = list(chunk_info_map.values())
    deduped.sort(key=cmp_to_key(sort_by_score_desc))
    return deduped


def rescale_unbounded_scores(results: Sequence[IndexWithScore]) -> None:
    """把单个检索器的候选分数按本条列表的最高分压进 [0, 1]（原地修改，无返回值）。

    为什么必须做：关键词（BM25）分数是无界的，原样留下会让 CompositeScore 的
    0.3*base 项在 [0,1] clamp 后把所有候选顶到 1.0，MMR 于是退化成只按多样性排序；
    而不同引擎、不同索引的 BM25 互不可比，所以只能按「各自列表的最高分」缩放，
    这样列表内部的相对顺序保持不变。

    已经在 [0, 1] 内的分数原样不动：有些引擎（Qdrant、Doris）把关键词命中直接标成
    1.0，向量分数本身也已归一化，动了就会改变它们的量级（FAQ 阈值吃的正是这个量级）。
    因为取样（retrieve / Langfuse）发生在融合之前，原始 BM25 在检索 span 里依然可见。

    边界与 Go 一致：NaN 和正负无穷不参与求最大值，+Inf 归一化后是 1，
    NaN / -Inf / 非正数归一化后是 0；最大值不大于 1 时整条列表直接返回不动，
    所以这两条规则只在确实存在大于 1 的有限分数时生效。
    """
    max_score = 0.0
    for r in results:
        if r is None:
            continue
        if math.isnan(r.score) or math.isinf(r.score):
            continue
        if r.score > max_score:
            max_score = r.score
    if max_score <= 1:
        return
    for r in results:
        if r is None:
            continue
        if math.isnan(r.score) or r.score == -math.inf or r.score <= 0:
            r.score = 0.0
        elif r.score == math.inf:
            # +Inf 表示「比任何有限分都更相关」，不是 0。
            r.score = 1.0
        else:
            r.score = r.score / max_score


def fuse_or_deduplicate(
    vector_lists: Sequence[Sequence[IndexWithScore]],
    keyword_lists: Sequence[Sequence[IndexWithScore]],
    retrieval_cfg: Optional[RetrievalConfig] = None,
) -> List[IndexWithScore]:
    """融合向量与关键词结果，或在只有单路时去重；每条路径都返回 [0, 1] 的分数。

    为什么要统一到 [0, 1]：若干独立搜索（按文档或标签目标、按 embedding 模型分组、
    FAQ 与文档各一次调用）的结果要在下游一起排序，量纲不同就没法比。三条分支：
      - 只有向量：引擎给的余弦相似度原样保留（FAQ 阈值依赖它）；
      - 只有关键词：BM25 除以本条列表的最高分（见 rescale_unbounded_scores）；
      - 两路都有：加权 RRF 除以理论最大值（见 fuse_with_rrf），两路都排第一的
        chunk 恰好得 1.0。

    retrieval_cfg 可以为 None，此时用默认 RRF 参数（k=60，权重 0.7/0.3）。
    """
    if not keyword_lists:
        return deduplicate_by_score(flatten_lists(vector_lists))
    if not vector_lists:
        for one_list in keyword_lists:
            rescale_unbounded_scores(one_list)
        return deduplicate_by_score(flatten_lists(keyword_lists))
    return fuse_with_rrf(vector_lists, keyword_lists, retrieval_cfg)


def fuse_with_rrf(
    vector_lists: Sequence[Sequence[IndexWithScore]],
    keyword_lists: Sequence[Sequence[IndexWithScore]],
    retrieval_cfg: Optional[RetrievalConfig] = None,
) -> List[IndexWithScore]:
    """加权倒数排名融合（RRF），分数归一到 [0, 1] 后按降序返回。

    公式：score = vectorWeight/(k+vectorRank) + keywordWeight/(k+keywordRank)，
    再除以 (vectorWeight+keywordWeight)/(k+1)。

    为什么要除掉这个理论最大值：原始 RRF 的峰值只有 0.016 左右，混检命中会因为
    量纲太小输给任何向量或关键词单路结果，并且让 rerank 的 base-score 项和 MMR 的
    相关性项几乎拿不到信息。归一后它与单路结果同处 [0, 1]，两路都排第一的 chunk
    恰好得 1.0。

    chunk 在某个 retriever 上的名次取 best_ranks（各列表各自排序去重后的最好名次），
    因为把多条列表拼起来之后的位置是任意的：第二条列表里的最好命中，否则会排到
    第一条列表的所有命中之后。

    与 Go 一致的两点细节：同一个 chunk 优先用向量列表里分数最高的那个对象（它的
    metadata 更全），关键词独有的 chunk 取首次出现的那条、即使后面出现更高分。
    """
    rrf_k = effective_rrf_k(retrieval_cfg)
    vector_weight, keyword_weight = effective_rrf_weights(retrieval_cfg)
    max_rrf = (vector_weight + keyword_weight) / float(rrf_k + 1)

    vector_ranks = best_ranks(vector_lists)
    keyword_ranks = best_ranks(keyword_lists)

    chunk_info_map: Dict[str, IndexWithScore] = {}
    for r in flatten_lists(vector_lists):
        if r is None:
            continue
        existing = chunk_info_map.get(r.chunk_id)
        if existing is None or r.score > existing.score:
            chunk_info_map[r.chunk_id] = r
    for r in flatten_lists(keyword_lists):
        if r is None:
            continue
        if r.chunk_id not in chunk_info_map:
            chunk_info_map[r.chunk_id] = r

    result: List[IndexWithScore] = []
    for chunk_id, info in chunk_info_map.items():
        rrf_score = 0.0
        vector_rank = vector_ranks.get(chunk_id)
        if vector_rank is not None:
            rrf_score += vector_weight / float(rrf_k + vector_rank)
        keyword_rank = keyword_ranks.get(chunk_id)
        if keyword_rank is not None:
            rrf_score += keyword_weight / float(rrf_k + keyword_rank)
        info.score = rrf_score / max_rrf
        result.append(info)
    result.sort(key=cmp_to_key(sort_by_score_desc))
    return result


def best_ranks(lists: Sequence[Sequence[IndexWithScore]]) -> Dict[str, int]:
    """每个 chunk 在这个 retriever 的多条列表里的最好名次（1-based）。

    每条列表各自先按分数排序并去重（同一 chunk 在同一列表里重复出现时保留最高分的
    那条），再取该 chunk 在所有列表中的最小名次。
    """
    ranks: Dict[str, int] = {}
    for one_list in lists:
        for i, r in enumerate(deduplicate_by_score(one_list)):
            current = ranks.get(r.chunk_id)
            if current is None or i + 1 < current:
                ranks[r.chunk_id] = i + 1
    return ranks


def select_mmr(
    results: Sequence[IndexWithScore],
    k: int,
    lambda_: float = DEFAULT_MMR_LAMBDA,
) -> List[int]:
    """用最大边际相关（MMR）挑出至多 k 个候选，返回它们在下标中的挑选顺序。

    为什么要 MMR：只按分数截断 top-k 会把同一段内容的多个切片或同一 chunk 的多路
    召回一起返回，占满预算却不多给信息。MMR 每轮在「lambda 乘相关性 减去
    (1-lambda) 乘与已选集合的最大相似度」上取最大者，lambda=0.7 表示相关性仍占
    主导、冗余只作为扣分项。

    与 Go 的实现一致：maxRedundancy 增量维护（每个候选只跟「新选中的那个」比一次
    Jaccard 再取大），所以每轮每个候选只需一次比较；平手时取更靠前的候选，因此
    第一个被选中的必然是相关性最高的那个。k 非正或没有候选时返回空列表。
    """
    if k <= 0 or not results:
        return []
    remaining = list(range(len(results)))
    token_sets = [tokenize_simple(r.content or "") for r in results]
    max_redundancy = [0.0] * len(results)
    selected: List[int] = []
    while len(selected) < k and remaining:
        best_pos = 0
        best_mmr = -math.inf
        for pos, i in enumerate(remaining):
            mmr = lambda_ * results[i].score - (1.0 - lambda_) * max_redundancy[i]
            if mmr > best_mmr:
                best_mmr = mmr
                best_pos = pos
        chosen = remaining.pop(best_pos)
        selected.append(chosen)
        for i in remaining:
            max_redundancy[i] = max(
                max_redundancy[i], jaccard(token_sets[i], token_sets[chosen])
            )
    return selected


def tokenize_terms(text: str) -> List[str]:
    """按检索用词边界切词，保留重复项供 BM25 计算词频。"""
    text = text.strip().lower()
    if not text:
        return []
    words = _cut_for_search(text) if _contains_han(text) else text.split()
    out: List[str] = []
    for word in words:
        word = word.strip()
        if len(word) > 1 and not _is_all_punct(word):
            out.append(word)
    return out


def tokenize_simple(text: str) -> Set[str]:
    """切词成小写 token 集合（对应 searchutil.TokenizeSimple）。

    中文走 jieba 的 cut_for_search，与 Go 的 CutForSearch 同源；jieba 缺席时
    退化成「CJK 连续片段取相邻二字（bigram）＋非 CJK 片段按空白切分」——
    Jaccard 只看重叠比例，bigram 一样能抓住「同一段文字重复」的信号，只是数值
    不会与 jieba 逐位相等。token 的过滤规则与 Go 一致：长度大于 1 且不是纯标点。
    """
    return set(tokenize_terms(text))


def jaccard(a: Set[str], b: Set[str]) -> float:
    """两个 token 集合的 Jaccard 相似度；两边都为空时返回 0（Go 也返回 0）。

    两个空 chunk 之间「什么都对不上」，不该被当成「完全一样」，否则 MMR 会把空文本
    当成高度冗余而互相压制。
    """
    if not a and not b:
        return 0.0
    if len(a) > len(b):
        return jaccard(b, a)
    inter = len(a & b)
    union = len(a) + len(b) - inter
    if union == 0:
        return 0.0
    return inter / union


def composite_score(
    candidate: IndexWithScore,
    model_score: float,
    base_score: Optional[float] = None,
) -> float:
    """把 rerank 模型分与检索分融成 [0, 1] 的综合分（对应 CompositeScore）。

    权重是 Go 里写死的 0.6*model + 0.3*base + 0.1*source：
      - 0.3*base 是检索分的贡献（向量相似度，或已被 rescale_unbounded_scores
        压回 [0,1] 的 BM25）——无界分数不先缩放，这一项会被 clamp 顶满、所有候选并列；
      - 0.1*source 是来源权重：web_search 结果给 0.95，其余 1.0；
      - graph 命中没有检索分（来自实体查询而不是相似度检索），用模型分顶替，而不是
        编一个常数。

    base_score 缺省取候选自己的分数。NaN 的处理与 Go 的 math.Max/math.Min 一致：
    NaN 会透传（Python 的 max/min 只在 NaN 作为第一个参数时透传，所以顺序固定为
    先 max(x, 0) 再 min(..., 1)）。
    """
    source_weight = (
        0.95 if (candidate.knowledge_source or "").lower() == _SOURCE_WEB_SEARCH else 1.0
    )
    if base_score is None:
        base_score = candidate.score
    if candidate.match_type == MATCH_TYPE_GRAPH:
        base_score = model_score
    composite = 0.6 * model_score + 0.3 * base_score + 0.1 * source_weight
    return min(max(composite, 0.0), 1.0)


def _contains_han(text: str) -> bool:
    return any(_is_cjk(ch) for ch in text)


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF  # CJK 统一表意文字
        or 0x3400 <= code <= 0x4DBF  # 扩展 A
        or 0xF900 <= code <= 0xFAFF  # 兼容表意文字
    )


@lru_cache(maxsize=1)
def _jieba_cut_for_search() -> Optional[Callable[[str], Iterable[str]]]:
    """取得 jieba 的搜索引擎模式分词函数；jieba 不可用时返回 None。

    惰性导入并缓存：jieba 首次调用要加载词典（几百毫秒、几十 MB 内存），
    不能让 import fusion 就付这个代价；但也不该每次分词都去查 sys.modules。
    缓存装饰器正好同时满足这两点。
    """
    try:
        import jieba
    except ImportError:
        return None
    return jieba.cut_for_search


def _cut_for_search(text: str) -> List[str]:
    cutter = _jieba_cut_for_search()
    if cutter is not None:
        return list(cutter(text))

    # jieba 缺席时的退化路径：CJK 片段切相邻二字，其余按空白切。
    words: List[str] = []
    for is_cjk_run, chunk in _split_runs(text):
        if is_cjk_run:
            words.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
        else:
            words.extend(chunk.split())
    return words


def _split_runs(text: str) -> Iterator[Tuple[bool, str]]:
    current_is_cjk = False
    buffer: List[str] = []
    for ch in text:
        is_cjk = _is_cjk(ch)
        if buffer and is_cjk != current_is_cjk:
            yield current_is_cjk, "".join(buffer)
            buffer = []
        current_is_cjk = is_cjk
        buffer.append(ch)
    if buffer:
        yield current_is_cjk, "".join(buffer)


def _is_all_punct(text: str) -> bool:
    # Go 用 unicode.IsPunct / IsSpace / IsSymbol 判断「整串都是标点」，
    # 对应 Unicode 类别 P* / Z* / S*。
    return all(unicodedata.category(ch)[0] in ("P", "Z", "S") for ch in text)
