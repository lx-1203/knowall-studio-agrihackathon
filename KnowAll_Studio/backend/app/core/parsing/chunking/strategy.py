"""自适应分块的公开入口。

调用方调用 split / split_parent_child，而不是 legacy 的
split_text / split_text_parent_child；策略解析器按文档画像与
SplitterConfig.strategy 提示挑一条 Tier 链。legacy 入口依然保留在
splitter.py 里，这里只是在它上面叠了一层 Tier 选择。
"""

from __future__ import annotations

import logging
from dataclasses import replace

from .heading_splitter import split_by_headings_impl
from .heuristic_splitter import split_by_heuristics_impl
from .profiler import profile_document, select_strategy
from .splitter import split_text
from .tokens import chars_for_token_limit
from .types import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_SEPARATORS,
    LANG_MIXED,
    STRATEGY_AUTO,
    STRATEGY_HEADING,
    STRATEGY_HEURISTIC,
    STRATEGY_LEGACY,
    STRATEGY_RECURSIVE,
    TIER_HEADING,
    TIER_HEURISTIC,
    TIER_LEGACY,
    ChildChunk,
    Chunk,
    Diagnostics,
    DocProfile,
    ParentChildResult,
    SplitterConfig,
    TierRejection,
)
from .validator import validate_chunks

logger = logging.getLogger(__name__)

# 父/子两级分块的默认窗口（knowledge 摄取链路使用的数值）。
DEFAULT_PARENT_SIZE = 4096
DEFAULT_CHILD_SIZE = 384


def split(text: str, cfg: SplitterConfig) -> list[Chunk]:
    """按 cfg 指定的策略切分。

    strategy 为空或 auto 时由文档画像决定 Tier。函数永远返回非 None 结果：
    某一档失败就顺着链路降级，最终落到 legacy（也就是最初的 Tier 3）。

    热路径：刻意不构造 Diagnostics，因为 SplitParentChild 会对每个父块
    调用一次本函数。
    """
    if text == "":
        return []
    cfg = ensure_defaults(cfg)
    chain, profile = resolve_chain_with_profile(text, cfg)
    total_chars = len(text)  # rune 数

    last_out: list[Chunk] | None = None
    for i, tier in enumerate(chain):
        out = run_tier(tier, text, cfg, profile)
        v = validate_chunks(out, total_chars, cfg.chunk_size)
        if v.ok:
            return out
        logger.debug("chunker: tier %s rejected: %s", tier, v.reason)
        if tier == TIER_LEGACY and i == len(chain) - 1:
            last_out = out
    if last_out is not None:
        return last_out
    return split_text(text, cfg)


def split_with_diagnostics(text: str, cfg: SplitterConfig) -> tuple[list[Chunk], Diagnostics]:
    """与 split 相同，但额外返回诊断轨迹（胜出 Tier、完整链路、拒绝原因、
    可用时的文档画像）。供 chunker preview 端点使用。"""
    # selected_tier 默认 legacy，避免空诊断出现零值字符串（debug UI 会显示成空标签）。
    diag = Diagnostics(selected_tier=TIER_LEGACY)
    if text == "":
        return [], diag
    cfg = ensure_defaults(cfg)
    chain, profile = resolve_chain_with_profile(text, cfg)
    diag.tier_chain = chain
    diag.profile = profile
    total_chars = len(text)

    last_out: list[Chunk] | None = None
    last_tier = TIER_LEGACY
    for i, tier in enumerate(chain):
        out = run_tier(tier, text, cfg, profile)
        v = validate_chunks(out, total_chars, cfg.chunk_size)
        if v.ok:
            diag.selected_tier = tier
            return out, diag
        diag.rejected.append(TierRejection(tier=tier, reason=v.reason))
        logger.debug("chunker: tier %s rejected: %s", tier, v.reason)
        if tier == TIER_LEGACY and i == len(chain) - 1:
            last_out = out
            last_tier = tier
    if last_out is not None:
        diag.selected_tier = last_tier
        return last_out, diag
    # 防御性最后兜底。
    return split_text(text, cfg), diag


def split_parent_child(text: str, parent_cfg: SplitterConfig, child_cfg: SplitterConfig) -> ParentChildResult:
    """策略感知版的两级分块。

    父块走 Tier 选择器；随后每个父块再用小块配置切成子块。

    子块切分尊重 child_cfg.strategy：为空/auto 且父块自身还有内部结构
    （子标题、编号子节）时，对应的 Tier 会接上，让子块带比父块更细的
    面包屑。对每个父块重新画像的总代价是 O(sum(parent_size)) ≈ O(N)，
    与原来只画像一次同阶。
    """
    result, _ = _split_parent_child(text, parent_cfg, child_cfg, False)
    return result


def split_parent_child_with_diagnostics(
    text: str, parent_cfg: SplitterConfig, child_cfg: SplitterConfig
) -> tuple[ParentChildResult, Diagnostics | None]:
    """split_parent_child 的诊断版：结果完全相同，诊断描述的是整篇文档的
    父块切分策略选择。面向调试界面，而不是摄取热路径。"""
    return _split_parent_child(text, parent_cfg, child_cfg, True)


def _split_parent_child(
    text: str, parent_cfg: SplitterConfig, child_cfg: SplitterConfig, with_diagnostics: bool
) -> tuple[ParentChildResult, Diagnostics | None]:
    parent_cfg = ensure_defaults(parent_cfg)
    child_cfg = ensure_defaults(child_cfg)

    parents: list[Chunk] = []
    diag: Diagnostics | None = None
    if with_diagnostics:
        parents, diag = split_with_diagnostics(text, parent_cfg)
    else:
        parents = split(text, parent_cfg)
    if not parents:
        return ParentChildResult(), diag

    new_parents: list[Chunk] = []
    children: list[ChildChunk] = []
    child_seq = 0
    for parent in parents:
        subs = split(parent.content, child_cfg)

        parent_index = -1
        if len(subs) > 1 or (len(subs) == 1 and subs[0].content != parent.content):
            parent_index = len(new_parents)
            new_parents.append(parent)
        for sub in subs:
            sub.seq = child_seq
            sub.start += parent.start
            sub.end += parent.start
            sub.context_header = merge_breadcrumbs(parent.context_header, sub.context_header)
            children.append(
                ChildChunk(
                    content=sub.content,
                    context_header=sub.context_header,
                    seq=sub.seq,
                    start=sub.start,
                    end=sub.end,
                    parent_index=parent_index,
                )
            )
            child_seq += 1
    return ParentChildResult(parents=new_parents, children=children), diag


def normalize_splitter_config(cfg: SplitterConfig) -> SplitterConfig:
    """套用 knowledge 摄取链路用到的基础默认值。"""
    # Go 的 SplitterConfig 是值语义，Python 需要显式拷贝，避免就地改写调用方对象。
    cfg = replace(cfg)
    if cfg.chunk_size <= 0:
        cfg.chunk_size = DEFAULT_CHUNK_SIZE
    if cfg.chunk_overlap <= 0:
        cfg.chunk_overlap = DEFAULT_CHUNK_OVERLAP
    if not cfg.separators:
        cfg.separators = list(DEFAULT_SEPARATORS)
    return cfg


def normalize_line_endings(text: str) -> str:
    """分块前规范化换行。

    浏览器 textarea 会把粘贴的 CRLF 归一成 LF，而上传文件保留原始换行；
    不做归一的话，同一份文档会产生不同的字符数与 chunk 边界。
    """
    if "\r" not in text:
        return text
    return text.replace("\r\n", "\n").replace("\r", "\n")


def derive_parent_child_configs(
    base: SplitterConfig, parent_size: int, child_size: int
) -> tuple[SplitterConfig, SplitterConfig]:
    """产出 knowledge 摄取链路真正使用的父/子切分配置。

    放在这里是为了让 preview 与摄取链路随着父子默认值的演进保持同步。
    languages 会复制到两级，让父子使用相同的边界规则；token_limit 只复制给
    子级，因为父级要保持配置的上下文窗口。
    """
    if parent_size <= 0:
        parent_size = DEFAULT_PARENT_SIZE
    if child_size <= 0:
        child_size = DEFAULT_CHILD_SIZE
    parent = SplitterConfig(
        chunk_size=parent_size,
        chunk_overlap=base.chunk_overlap,
        separators=base.separators,
        strategy=base.strategy,
        languages=base.languages,
    )
    child = SplitterConfig(
        chunk_size=child_size,
        chunk_overlap=child_size // 5,
        separators=base.separators,
        strategy=base.strategy,
        token_limit=base.token_limit,
        languages=base.languages,
    )
    return parent, child


def merge_breadcrumbs(parent: str, child: str) -> str:
    """合并父/子标题面包屑成一个 ContextHeader。

    子块是在父块内容上重新做标题检测的，它的第一行面包屑常常与父块的最后
    一行重复（父块的首个标题就在子块输入的顶部）；把这条重复行去掉，
    embedding 上下文才不会冗余。
    """
    if parent == "":
        return child
    if child == "":
        return parent
    parent_lines = parent.split("\n")
    child_lines = child.split("\n")
    if parent_lines and child_lines and parent_lines[-1].strip() == child_lines[0].strip():
        child_lines = child_lines[1:]
    if not child_lines:
        return parent
    return parent + "\n" + "\n".join(child_lines)


def resolve_chain_with_profile(text: str, cfg: SplitterConfig) -> tuple[list[str], DocProfile | None]:
    """返回要尝试的策略链，以及（仅当 auto 策略由画像驱动时）画像本身。

    显式指定非 auto 策略时 profile 为 None，调用方不必为没用上的画像扫描付费。
    """
    strat = cfg.strategy
    if strat == STRATEGY_HEADING:
        return [TIER_HEADING, TIER_LEGACY], None
    if strat == STRATEGY_HEURISTIC:
        return [TIER_HEURISTIC, TIER_LEGACY], None
    if strat == STRATEGY_RECURSIVE:
        # "recursive" 是 "legacy" 的公开 API 别名：两者都走 SplitText。
        # 保留是为了兼容已存储的配置。
        return [TIER_LEGACY], None
    if strat in (STRATEGY_LEGACY, ""):
        # 空 == legacy，兼容 Strategy 字段出现之前入库的 ChunkingConfig 行。
        return [TIER_LEGACY], None
    # StrategyAuto 及未知值
    profile = profile_document(text)
    return select_strategy(profile), profile


def run_tier(tier: str, text: str, cfg: SplitterConfig, profile: DocProfile | None) -> list[Chunk]:
    """按 Tier 分派具体实现；legacy 走 split_text。

    profile 在显式非 auto 策略下可能为 None，需要它的实现会自己按需计算。
    """
    if tier == TIER_HEADING:
        return split_by_headings_impl(text, cfg, profile)
    if tier == TIER_HEURISTIC:
        return split_by_heuristics_impl(text, cfg, profile)
    if tier == TIER_LEGACY:
        return split_text(text, cfg)
    return split_text(text, cfg)


def ensure_defaults(cfg: SplitterConfig) -> SplitterConfig:
    """把零值配置字段填成合理默认值。

    normalize_splitter_config 用的是同一套默认值，保证服务调用方与直接调用
    本包的人拿到同样的数字。

    设置了 token_limit 时，chunk_size 会被钳到该 token 上限对应的字符预算
    （含 10% 安全系数），让 chunk 对硬性 token 上限的 embedding API 安全。
    """
    # Go 的 SplitterConfig 是值语义，Python 需要显式拷贝，避免就地改写调用方对象。
    cfg = replace(cfg)
    if cfg.chunk_size <= 0:
        cfg.chunk_size = DEFAULT_CHUNK_SIZE
    if cfg.chunk_overlap <= 0:
        cfg.chunk_overlap = DEFAULT_CHUNK_OVERLAP
    if not cfg.separators:
        cfg.separators = list(DEFAULT_SEPARATORS)
    if cfg.token_limit > 0:
        lang = LANG_MIXED
        if cfg.languages:
            lang = cfg.languages[0]
        char_budget = chars_for_token_limit(cfg.token_limit, lang)
        if char_budget > 0 and (cfg.chunk_size == 0 or char_budget < cfg.chunk_size):
            cfg.chunk_size = char_budget
    # 防病态 overlap：overlap 超过 chunk_size 的一半时几乎每个 chunk 都是
    # 重复内容。钳到 chunk_size/2，让重叠保持成一条有用的平滑带。
    if cfg.chunk_overlap > cfg.chunk_size // 2 and cfg.chunk_size > 0:
        cfg.chunk_overlap = cfg.chunk_size // 2
    return cfg


# Go 导出名别名
Split = split
SplitWithDiagnostics = split_with_diagnostics
SplitParentChild = split_parent_child
SplitParentChildWithDiagnostics = split_parent_child_with_diagnostics
NormalizeSplitterConfig = normalize_splitter_config
NormalizeLineEndings = normalize_line_endings
DeriveParentChildConfigs = derive_parent_child_configs
MergeBreadcrumbs = merge_breadcrumbs
ResolveChainWithProfile = resolve_chain_with_profile
RunTier = run_tier
EnsureDefaults = ensure_defaults
StrategyAuto = STRATEGY_AUTO
StrategyHeading = STRATEGY_HEADING
StrategyHeuristic = STRATEGY_HEURISTIC
StrategyRecursive = STRATEGY_RECURSIVE
StrategyLegacy = STRATEGY_LEGACY
