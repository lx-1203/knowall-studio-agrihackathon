"""分块引擎的纯数据类型与共享常量。

本模块对应 Go 版 chunker 包里散落在各文件的零值结构体
（Chunk / SplitterConfig / DocProfile / Diagnostics / ValidationResult /
span / splitUnit / boundary）。集中放置是为了打断 Go 的包级耦合：
Python 模块之间不允许 import 成环。

Go 的零值语义被原样保留 —— SplitterConfig() 等价于 Go 的
SplitterConfig{}（chunk_size=0、chunk_overlap=0、separators 为空），
默认值由 ensure_defaults 填充，而不是在这里预置；因为 Go 的
SplitText 与各 Tier 实现（heading/heuristic）对“未配置”的处理并不一致，
提前填默认值会改变行为。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- splitter.go: DefaultChunkSize / DefaultChunkOverlap -------------------
# 512 字符 ≈ 100~130 英文 token / ~300 中文 token，是 Vecta Feb-2026
# 基准在 50 篇学术论文上验证过的基线。80 字符 ≈ 15%，是召回率与存储
# 成本的社区共识平衡点（历史上 Go/Python 曾有三套不同的 overlap 默认值）。
DEFAULT_CHUNK_SIZE = 512
DEFAULT_CHUNK_OVERLAP = 80

# token 预算超出 ChunkSize/2 时，几乎每个 chunk 都是前一个的近克隆，
# 因此 overlap 上限被钳到 ChunkSize/2。
MAX_OVERLAP_RATIO = 2

# --- tokens.go: 语言标识 ---------------------------------------------------
LANG_ENGLISH = "en"
LANG_GERMAN = "de"
LANG_CHINESE = "zh"
LANG_MIXED = "mixed"

# --- strategy.go: SplitterConfig.Strategy 取值 -----------------------------
STRATEGY_AUTO = "auto"
STRATEGY_HEADING = "heading"
STRATEGY_HEURISTIC = "heuristic"
STRATEGY_RECURSIVE = "recursive"
STRATEGY_LEGACY = "legacy"

# --- profiler.go: StrategyTier --------------------------------------------
TIER_HEADING = "heading"
TIER_HEURISTIC = "heuristic"
TIER_LEGACY = "legacy"

# --- patterns.go: 启发式边界优先级（数值越大越强） -------------------------
PRIO_FORM_FEED = 100
PRIO_NUMBERED_HEAD = 90
PRIO_CHAPTER_MARKER = 85
PRIO_ALL_CAPS_HEADING = 70
PRIO_VISUAL_SEP = 60
PRIO_PAGE_FOOTER = 50
PRIO_BLANK_BLOCK = 40

# Go 的 DefaultConfig() 里的分隔符优先级顺序。
DEFAULT_SEPARATORS: tuple[str, ...] = ("\n\n", "\n", "。")

# splitter.go: 单个 unit / 保护区被强制切开的硬上限（留余量给标题等）。
ABSOLUTE_MAX_SIZE = 7500
MAX_PROTECTED_SIZE = 7500

# header_tracker.go: 当前唯一的表头 hook 优先级。
MARKDOWN_TABLE_HOOK_PRIORITY = 15


@dataclass
class Span:
    """区间。protected_spans 返回的是 UTF-8 字节偏移，protected_spans_rune
    返回的是 rune（= Python 字符）偏移，两者不可混用。"""

    start: int
    end: int


@dataclass
class SplitUnit:
    """splitter.go: 带原始位置的最小切分单元。start/end 是 rune 偏移。"""

    text: str
    start: int
    end: int


@dataclass
class Boundary:
    """heuristic_splitter.go: 候选切分点（rune 偏移）。"""

    rune_start: int
    priority: int


@dataclass
class HeadingBoundary:
    """heading_splitter.go: 小节起点。line 为空表示这是文首的隐含边界。"""

    rune_start: int
    line: str = ""


@dataclass
class SectionBreadcrumb:
    """heading_splitter.go: 小节内部某个 rune 偏移起生效的面包屑。"""

    rune_start: int
    breadcrumb: str


@dataclass
class Chunk:
    """一段切分结果 + 位置追踪。

    content 恰好等于原文 [start, end) 之间的文本（rune 偏移），即
    end - start == len(content)。文档重建逻辑依赖这条不变式，因此
    context_header（如 Markdown 标题面包屑）被单独保存：它在 embedding
    时才拼接，不计入 content，从而不破坏位置不变式。
    """

    content: str = ""
    context_header: str = ""
    seq: int = 0
    start: int = 0
    end: int = 0

    def embedding_content(self) -> str:
        """喂给 embedding 模型的文本：context_header（若有）前置。

        content 本身必须保持原文原样（位置不变式要求），但 embedding 时
        需要去掉边界切片带来的首尾空白，避免稀释向量、浪费 token。
        """
        body = self.content.strip()
        if not self.context_header:
            return body
        return self.context_header + "\n\n" + body

    # --- Go 导出名别名，仅用于与上游对照 -------------------------------
    @property
    def Content(self) -> str:
        return self.content

    @property
    def ContextHeader(self) -> str:
        return self.context_header

    @property
    def Seq(self) -> int:
        return self.seq

    @property
    def Start(self) -> int:
        return self.start

    @property
    def End(self) -> int:
        return self.end

    def EmbeddingContent(self) -> str:
        return self.embedding_content()


@dataclass
class ChildChunk(Chunk):
    """在 Chunk 之上附带父块索引（Go 用结构体嵌入表达同一件事）。"""

    parent_index: int = -1

    @property
    def ParentIndex(self) -> int:
        return self.parent_index


@dataclass
class ParentChildResult:
    """两级分块输出：parents 提供上下文窗口，children 用于 embedding/检索。"""

    parents: list[Chunk] = field(default_factory=list)
    children: list[ChildChunk] = field(default_factory=list)

    @property
    def Parents(self) -> list[Chunk]:
        return self.parents

    @property
    def Children(self) -> list[ChildChunk]:
        return self.children


@dataclass
class SplitterConfig:
    """切分配置。strategy / token_limit 只被 strategy.py 的入口消费；
    legacy 的 SplitText 只用 chunk_size / chunk_overlap / separators。"""

    chunk_size: int = 0
    chunk_overlap: int = 0
    separators: list[str] = field(default_factory=list)

    # 空字符串 = legacy（与存量配置向后兼容）；可选值见 STRATEGY_*。
    strategy: str = ""
    # 近似 token 上限，0 表示以 chunk_size 字符为准。
    token_limit: int = 0
    # 多语言启发式提示，空表示自动探测。
    languages: list[str] = field(default_factory=list)

    @property
    def ChunkSize(self) -> int:
        return self.chunk_size

    @property
    def ChunkOverlap(self) -> int:
        return self.chunk_overlap

    @property
    def Separators(self) -> list[str]:
        return self.separators

    @property
    def Strategy(self) -> str:
        return self.strategy

    @property
    def TokenLimit(self) -> int:
        return self.token_limit

    @property
    def Languages(self) -> list[str]:
        return self.languages


def default_config() -> SplitterConfig:
    """splitter.go: DefaultConfig()。"""
    return SplitterConfig(
        chunk_size=DEFAULT_CHUNK_SIZE,
        chunk_overlap=DEFAULT_CHUNK_OVERLAP,
        separators=list(DEFAULT_SEPARATORS),
    )


@dataclass
class DocProfile:
    """文档画像：决定走 heading / heuristic / recursive 哪条链路。

    字段名（snake_case）是 preview 端点 JSON 契约的一部分，保持稳定。
    """

    total_chars: int = 0
    total_lines: int = 0
    avg_line_len: float = 0.0
    std_line_len: float = 0.0

    # Markdown 结构
    md_heading_counts: dict[int, int] = field(default_factory=dict)
    md_heading_total: int = 0

    # 启发式指标
    numbered_section_count: int = 0
    all_caps_short_line_count: int = 0
    blank_paragraph_breaks: int = 0
    form_feed_count: int = 0
    visual_sep_count: int = 0
    german_chapter_count: int = 0
    english_chapter_count: int = 0
    chinese_chapter_count: int = 0
    repeated_footer_count: int = 0

    # 内容特征
    has_tables: bool = False
    has_code: bool = False
    code_ratio: float = 0.0

    # 语言提示（尽力而为）
    detected_langs: list[str] = field(default_factory=list)

    def heading_density(self) -> float:
        if self.total_lines == 0:
            return 0.0
        return self.md_heading_total / self.total_lines

    def dominant_heading_level(self) -> int:
        """驱动分节的主导标题层级。

        优先级：出现 >= 3 次的最浅层级（真正的结构主干）；否则取存在的
        最深层级（小文档只有 1 个 H1 + 几个 H2 时，需要更细的边界）。
        没有任何 Markdown 标题时返回 0。
        """
        if self.md_heading_total == 0:
            return 0
        for level in range(1, 7):
            if self.md_heading_counts.get(level, 0) >= 3:
                return level
        for level in range(6, 0, -1):
            if self.md_heading_counts.get(level, 0) > 0:
                return level
        return 0

    def heuristic_marker_total(self) -> int:
        return (
            self.numbered_section_count
            + self.german_chapter_count
            + self.english_chapter_count
            + self.chinese_chapter_count
            + self.all_caps_short_line_count
            + self.visual_sep_count
            + self.form_feed_count
        )

    # --- Go 导出名别名 ---------------------------------------------------
    def HeadingDensity(self) -> float:
        return self.heading_density()

    def DominantHeadingLevel(self) -> int:
        return self.dominant_heading_level()

    def HeuristicMarkerTotal(self) -> int:
        return self.heuristic_marker_total()


@dataclass
class TierRejection:
    """某个 Tier 被 validator 拒绝的原因（供 debug/preview 端点展示）。"""

    tier: str
    reason: str


@dataclass
class Diagnostics:
    """记录最终胜出的 Tier、尝试过的链路、被拒记录与驱动的文档画像。"""

    selected_tier: str = TIER_LEGACY
    tier_chain: list[str] = field(default_factory=list)
    rejected: list[TierRejection] = field(default_factory=list)
    # 只有 auto 策略真的跑了 profiler 时才非 None。
    profile: DocProfile | None = None


@dataclass
class ValidationResult:
    ok: bool = False
    reason: str = ""
