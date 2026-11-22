"""文档画像：一次扫描收集结构信号，决定用哪条分块链路。

画像很便宜（几遍正则 + rune 计数），并且必须在任何分块决策之前完成，
这样 auto 策略只扫一遍文档就能挑出 Tier，而不是让每个 Tier 各扫一遍。
"""

from __future__ import annotations

import math

from .patterns import (
    AllCapsHeadingPattern,
    ChineseChapterPattern,
    EnglishChapterPattern,
    GermanChapterPattern,
    MarkdownHeadingPattern,
    NumberedSectionPattern,
    PageFooterPattern,
    VisualSeparatorPattern,
)
from .tokens import detect_language
from .types import (
    LANG_CHINESE,
    LANG_ENGLISH,
    LANG_GERMAN,
    LANG_MIXED,
    TIER_HEADING,
    TIER_HEURISTIC,
    TIER_LEGACY,
    DocProfile,
)

# 语言探测只看文档前 4096 字节，避免在超大输入上付出 O(N) 代价
# 却只为了得到一个稳定信号。
LANG_SAMPLE_BYTES = 4096

# 围栏代码块的起始标记。Go 里是裸字面量 ```，这里同样如此。
FENCE = "```"


def profile_document(text: str) -> DocProfile:
    """单趟扫描并返回文档画像。"""
    p = DocProfile(md_heading_counts={})
    if text == "":
        return p

    # Go: len([]rune(text)) == Python 的 len(text)
    p.total_chars = len(text)
    p.form_feed_count = text.count("\f")

    lines = text.split("\n")
    p.total_lines = len(lines)

    lengths: list[float] = []
    in_fence = False
    code_chars = 0
    for line in lines:
        trimmed = line.strip()

        # 只用三反引号前缀判断围栏，而不是复用完整的受保护模式匹配，
        # 免得后面还要和 protectedPatterns 的逻辑打架。
        if trimmed.startswith(FENCE):
            in_fence = not in_fence
            p.has_code = True
            continue
        if in_fence:
            code_chars += len(line)
            continue

        lengths.append(float(len(line)))

        if match_heading(line, p.md_heading_counts):
            p.md_heading_total += 1
            continue
        if NumberedSectionPattern.search(line):
            p.numbered_section_count += 1
        if GermanChapterPattern.search(line):
            p.german_chapter_count += 1
        if EnglishChapterPattern.search(line):
            p.english_chapter_count += 1
        if ChineseChapterPattern.search(line):
            p.chinese_chapter_count += 1
        if AllCapsHeadingPattern.search(line):
            p.all_caps_short_line_count += 1
        if VisualSeparatorPattern.search(line):
            p.visual_sep_count += 1
        if PageFooterPattern.search(line):
            p.repeated_footer_count += 1
        if trimmed.startswith("|") and trimmed.endswith("|"):
            p.has_tables = True

    if lengths:
        total = sum(lengths)
        p.avg_line_len = total / len(lengths)
        # 总体方差（除以 n），与 Go 一致。
        variance = 0.0
        for l in lengths:
            d = l - p.avg_line_len
            variance += d * d
        variance /= len(lengths)
        p.std_line_len = math.sqrt(variance)

    if p.total_chars > 0:
        p.code_ratio = code_chars / p.total_chars

    p.blank_paragraph_breaks = text.count("\n\n\n")

    # Go 按字节截断样本。
    if len(text.encode("utf-8")) > LANG_SAMPLE_BYTES:
        sample = text.encode("utf-8")[:LANG_SAMPLE_BYTES].decode("utf-8", errors="ignore")
    else:
        sample = text
    lang = detect_language(sample)
    p.detected_langs = [lang]
    if lang == LANG_MIXED:
        # 混合文档把三种语言都给下游，让章节模式全部启用。
        p.detected_langs = [LANG_ENGLISH, LANG_GERMAN, LANG_CHINESE]

    return p


def match_heading(line: str, counts: dict[int, int]) -> bool:
    """命中 ATX 标题时递增对应层级的计数。"""
    m = MarkdownHeadingPattern.search(line)
    if m is None:
        return False
    level = len(m.group(1))
    if level < 1 or level > 6:
        return False
    counts[level] = counts.get(level, 0) + 1
    return True


def select_strategy(p: DocProfile | None) -> list[str]:
    """返回本次要尝试的 Tier 链。

    第一个 Tier 是首选，后续是 validator 拒绝时的降级备选。legacy 永远
    作为兜底追加在最后，保证调用方至少拿到一份分块结果。
    """
    if p is None:
        return [TIER_LEGACY]
    chain: list[str] = []

    # Tier 1 候选：Markdown 标题感知
    if p.md_heading_total >= 3 and p.heading_density() > 0.005 and p.dominant_heading_level() > 0:
        chain.append(TIER_HEADING)

    # Tier 2 候选：启发式边界
    if (
        p.heuristic_marker_total() >= 5
        or p.form_feed_count > 0
        or (p.german_chapter_count + p.english_chapter_count + p.chinese_chapter_count) > 0
    ):
        chain.append(TIER_HEURISTIC)

    # legacy 是最终兜底：即使校验失败也总能返回 chunk，调用方不会拿到空结果。
    chain.append(TIER_LEGACY)
    return chain


# Go 导出名别名
ProfileDocument = profile_document
SelectStrategy = select_strategy
