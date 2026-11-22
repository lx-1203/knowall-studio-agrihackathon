"""纯 Python 版的文本分块引擎（移植自腾讯 WeKnora 的 Go chunker 包）。

公开 API：
    split / split_with_diagnostics / split_parent_child
    SplitterConfig / Chunk / ChildChunk / ParentChildResult / Diagnostics
    normalize_line_endings / detect_language / approx_token_count / validate_chunks

本包是纯函数 + 纯数据结构：没有 async、没有网络、没有 DB、没有文件 IO，
只依赖标准库（re / math / dataclasses / typing / logging）。
"""

from __future__ import annotations

from .header_tracker import (
    HeaderTracker,
    header_column_mismatch,
    header_table_column_count,
    new_header_tracker,
    table_row_column_count,
)
from .heading_hierarchy import HeadingHierarchy, new_heading_hierarchy
from .heading_splitter import (
    breadcrumb_at_offset,
    coalesce_tiny_chunks,
    common_heading_prefix,
    find_heading_boundaries,
    split_by_headings_impl,
)
from .heuristic_splitter import (
    apply_overlap_aligned,
    drop_bounds_inside_spans,
    find_heuristic_boundaries,
    split_by_heuristics_impl,
)
from .patterns import (
    AllCapsHeadingPattern,
    ChineseChapterPattern,
    EnglishChapterPattern,
    ExcessiveBlanksPattern,
    FormFeedPattern,
    GermanChapterPattern,
    MarkdownHeadingPattern,
    NumberedSectionPattern,
    PageFooterPattern,
    VisualSeparatorPattern,
    chapter_patterns_for_langs,
    sentence_separators,
)
from .profiler import profile_document, select_strategy
from .splitter import (
    build_units_with_protection,
    compute_overlap,
    find_semantic_overlap_boundary,
    find_semantic_overlap_boundary_ending_at_or_after,
    merge_units,
    protected_spans,
    protected_spans_rune,
    rune_len,
    split_by_separators,
    split_text,
    split_text_parent_child,
    units_text,
)
from .strategy import (
    derive_parent_child_configs,
    ensure_defaults,
    merge_breadcrumbs,
    normalize_line_endings,
    normalize_splitter_config,
    resolve_chain_with_profile,
    run_tier,
    split,
    split_parent_child,
    split_parent_child_with_diagnostics,
    split_with_diagnostics,
)
from .tokens import (
    chars_for_token_limit,
    detect_language,
    approx_token_count,
    approx_token_count_from_rune_len,
)
from .types import (
    ABSOLUTE_MAX_SIZE,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_SEPARATORS,
    LANG_CHINESE,
    LANG_ENGLISH,
    LANG_GERMAN,
    LANG_MIXED,
    MAX_PROTECTED_SIZE,
    PRIO_ALL_CAPS_HEADING,
    PRIO_BLANK_BLOCK,
    PRIO_CHAPTER_MARKER,
    PRIO_FORM_FEED,
    PRIO_NUMBERED_HEAD,
    PRIO_PAGE_FOOTER,
    PRIO_VISUAL_SEP,
    STRATEGY_AUTO,
    STRATEGY_HEADING,
    STRATEGY_HEURISTIC,
    STRATEGY_LEGACY,
    STRATEGY_RECURSIVE,
    TIER_HEADING,
    TIER_HEURISTIC,
    TIER_LEGACY,
    Boundary,
    ChildChunk,
    Chunk,
    Diagnostics,
    DocProfile,
    HeadingBoundary,
    ParentChildResult,
    SectionBreadcrumb,
    Span,
    SplitUnit,
    SplitterConfig,
    TierRejection,
    ValidationResult,
    default_config,
)
from .validator import validate_chunks

__all__ = [
    # 公开 API
    "SplitterConfig",
    "Chunk",
    "ChildChunk",
    "SplitUnit",
    "Span",
    "Boundary",
    "HeadingBoundary",
    "SectionBreadcrumb",
    "ParentChildResult",
    "Diagnostics",
    "TierRejection",
    "ValidationResult",
    "DocProfile",
    "split",
    "split_with_diagnostics",
    "split_parent_child",
    "split_parent_child_with_diagnostics",
    "normalize_line_endings",
    "detect_language",
    "approx_token_count",
    "validate_chunks",
    "default_config",
    # 策略常量
    "STRATEGY_AUTO",
    "STRATEGY_HEADING",
    "STRATEGY_HEURISTIC",
    "STRATEGY_RECURSIVE",
    "STRATEGY_LEGACY",
    "TIER_HEADING",
    "TIER_HEURISTIC",
    "TIER_LEGACY",
    "LANG_ENGLISH",
    "LANG_GERMAN",
    "LANG_CHINESE",
    "LANG_MIXED",
    "PRIO_FORM_FEED",
    "PRIO_NUMBERED_HEAD",
    "PRIO_CHAPTER_MARKER",
    "PRIO_ALL_CAPS_HEADING",
    "PRIO_VISUAL_SEP",
    "PRIO_PAGE_FOOTER",
    "PRIO_BLANK_BLOCK",
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_CHUNK_OVERLAP",
    "DEFAULT_SEPARATORS",
    # 内部构件（测试与高级调用方使用）
    "split_text",
    "split_text_parent_child",
    "split_by_headings_impl",
    "split_by_heuristics_impl",
    "build_units_with_protection",
    "merge_units",
    "split_by_separators",
    "compute_overlap",
    "find_semantic_overlap_boundary",
    "find_semantic_overlap_boundary_ending_at_or_after",
    "protected_spans",
    "protected_spans_rune",
    "units_text",
    "rune_len",
    "profile_document",
    "select_strategy",
    "ensure_defaults",
    "resolve_chain_with_profile",
    "run_tier",
    "merge_breadcrumbs",
    "normalize_splitter_config",
    "derive_parent_child_configs",
    "chars_for_token_limit",
    "approx_token_count_from_rune_len",
    "HeadingHierarchy",
    "new_heading_hierarchy",
    "HeaderTracker",
    "new_header_tracker",
    "header_column_mismatch",
    "header_table_column_count",
    "table_row_column_count",
    "coalesce_tiny_chunks",
    "common_heading_prefix",
    "find_heading_boundaries",
    "breadcrumb_at_offset",
    "find_heuristic_boundaries",
    "drop_bounds_inside_spans",
    "apply_overlap_aligned",
    "sentence_separators",
    "chapter_patterns_for_langs",
]

# Markdown / 结构正则（Go 里是包级导出变量）
__all__ += [
    "MarkdownHeadingPattern",
    "FormFeedPattern",
    "NumberedSectionPattern",
    "AllCapsHeadingPattern",
    "VisualSeparatorPattern",
    "ExcessiveBlanksPattern",
    "PageFooterPattern",
    "GermanChapterPattern",
    "EnglishChapterPattern",
    "ChineseChapterPattern",
    "MAX_PROTECTED_SIZE",
    "ABSOLUTE_MAX_SIZE",
]

# --- Go 原名别名（便于与上游逐行对照） -------------------------------------
Split = split
SplitText = split_text
SplitWithDiagnostics = split_with_diagnostics
SplitParentChild = split_parent_child
SplitTextParentChild = split_text_parent_child
NormalizeLineEndings = normalize_line_endings
DetectLanguage = detect_language
ApproxTokenCount = approx_token_count
ApproxTokenCountFromRuneLen = approx_token_count_from_rune_len
CharsForTokenLimit = chars_for_token_limit
ValidateChunks = validate_chunks
ProfileDocument = profile_document
SelectStrategy = select_strategy
EnsureDefaults = ensure_defaults
NormalizeSplitterConfig = normalize_splitter_config
DeriveParentChildConfigs = derive_parent_child_configs
MergeBreadcrumbs = merge_breadcrumbs
NewHeadingHierarchy = new_heading_hierarchy
NewHeaderTracker = new_header_tracker
CommonHeadingPrefix = common_heading_prefix
CoalesceTinyChunks = coalesce_tiny_chunks
DropBoundsInsideSpans = drop_bounds_inside_spans
FindHeuristicBoundaries = find_heuristic_boundaries
FindHeadingBoundaries = find_heading_boundaries
ApplyOverlapAligned = apply_overlap_aligned
BuildUnitsWithProtection = build_units_with_protection
MergeUnits = merge_units
SplitBySeparators = split_by_separators
ProtectedSpans = protected_spans
ProtectedSpansRune = protected_spans_rune
UnitsText = units_text
ComputeOverlap = compute_overlap
FindSemanticOverlapBoundary = find_semantic_overlap_boundary
FindSemanticOverlapBoundaryEndingAtOrAfter = find_semantic_overlap_boundary_ending_at_or_after
SentenceSeparators = sentence_separators
ChapterPatternsForLangs = chapter_patterns_for_langs
StrategyAuto = STRATEGY_AUTO
StrategyHeading = STRATEGY_HEADING
StrategyHeuristic = STRATEGY_HEURISTIC
StrategyRecursive = STRATEGY_RECURSIVE
StrategyLegacy = STRATEGY_LEGACY
TierHeading = TIER_HEADING
TierHeuristic = TIER_HEURISTIC
TierLegacy = TIER_LEGACY
LangEnglish = LANG_ENGLISH
LangGerman = LANG_GERMAN
LangChinese = LANG_CHINESE
LangMixed = LANG_MIXED
PrioFormFeed = PRIO_FORM_FEED
PrioNumberedHead = PRIO_NUMBERED_HEAD
PrioChapterMarker = PRIO_CHAPTER_MARKER
PrioAllCapsHeading = PRIO_ALL_CAPS_HEADING
PrioVisualSep = PRIO_VISUAL_SEP
PrioPageFooter = PRIO_PAGE_FOOTER
PrioBlankBlock = PRIO_BLANK_BLOCK
DefaultChunkSize = DEFAULT_CHUNK_SIZE
DefaultConfig = default_config
DefaultChunkOverlap = DEFAULT_CHUNK_OVERLAP
