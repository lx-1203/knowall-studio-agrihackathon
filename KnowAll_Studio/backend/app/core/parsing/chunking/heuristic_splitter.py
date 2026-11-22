"""Tier 2：面向“没有规范 Markdown 标题、但有可识别结构线索”的文档。

线索包括分页符、编号小节、多语言章节标记、视觉分隔线、全大写小标题、
页脚。算法先找出全部候选边界，再做贪心装箱：把边界之间的块累加进 chunk，
直到下一块会超预算。比 ChunkSize 还大的块递归交给 legacy 切分器。
"""

from __future__ import annotations

from .patterns import (
    AllCapsHeadingPattern,
    ExcessiveBlanksPattern,
    NumberedSectionPattern,
    PageFooterPattern,
    VisualSeparatorPattern,
    chapter_patterns_for_langs,
)
from .profiler import FENCE
from .splitter import protected_spans, protected_spans_rune, split_text
from .types import (
    PRIO_ALL_CAPS_HEADING,
    PRIO_BLANK_BLOCK,
    PRIO_CHAPTER_MARKER,
    PRIO_FORM_FEED,
    PRIO_NUMBERED_HEAD,
    PRIO_PAGE_FOOTER,
    PRIO_VISUAL_SEP,
    Boundary,
    Chunk,
    DocProfile,
    Span,
    SplitterConfig,
)

# 最小 chunk 尺寸下限：决定贪心装箱在何处可以 flush。
MIN_CHUNK_SIZE_FLOOR = 50


def split_by_heuristics_impl(text: str, cfg: SplitterConfig, profile: DocProfile | None = None) -> list[Chunk]:
    """Tier-2 实现。找不到启发式边界时回落到 legacy 切分器。

    profile 目前未被使用（这一档直接扫描文档找边界），保留参数是为了让
    split_by_headings / split_by_heuristics 的签名一致。
    """
    if text == "":
        return []
    runes = list(text)
    total_runes = len(runes)
    if total_runes <= cfg.chunk_size:
        return split_text(text, cfg)

    bounds = find_heuristic_boundaries(text, cfg.languages)
    # 丢掉严格落在保护区内部的边界（表格、围栏代码块、LaTeX 块等）：
    # 在那切会切开原子内容。落在保护区边缘的边界保留，因为它与保护区
    # 起止对齐。
    prot = protected_spans_rune(text, protected_spans(text))
    if prot:
        bounds = drop_bounds_inside_spans(bounds, prot)
    if not bounds:
        return split_text(text, cfg)

    # 在文末加一个哨兵边界，方便装箱器收尾 flush。
    bounds.append(Boundary(rune_start=total_runes, priority=0))
    # 起点不是 0 时补一个 0 边界。
    if bounds[0].rune_start != 0:
        bounds.insert(0, Boundary(rune_start=0, priority=0))

    # 贪心装箱。
    out: list[Chunk] = []
    seq = 0
    chunk_start = bounds[0].rune_start
    cur_end = chunk_start
    min_chunk_size = cfg.chunk_size // 4
    if min_chunk_size < MIN_CHUNK_SIZE_FLOOR:
        min_chunk_size = MIN_CHUNK_SIZE_FLOOR

    for i in range(1, len(bounds)):
        next_end = bounds[i].rune_start
        block_len = next_end - cur_end

        if block_len > cfg.chunk_size:
            # 上一条边界到这一条之间的块本身就放不进任何 chunk：
            # 先 flush 当前累积，再把超大块交给 legacy 切分器递归处理。
            if cur_end - chunk_start > 0:
                seq = append_chunk(out, runes, chunk_start, cur_end, seq)
                chunk_start = cur_end
            seq = append_oversize_block(out, runes, cur_end, next_end, cfg, seq)
            cur_end = next_end
            chunk_start = next_end
            continue

        # 加上这一块会不会超预算？
        accumulated = next_end - chunk_start
        if accumulated > cfg.chunk_size and cur_end - chunk_start >= min_chunk_size:
            # 把累积内容 flush 成一个 chunk，并从 curEnd 重新开始。
            seq = append_chunk(out, runes, chunk_start, cur_end, seq)
            # 重叠起点吸附到最近的语义边界或换行处，避免切在行/词中间。
            chunk_start = apply_overlap_aligned(runes, cur_end, cfg.chunk_overlap, bounds)
        cur_end = next_end

    # 收尾 flush。
    if cur_end > chunk_start:
        seq = append_chunk(out, runes, chunk_start, cur_end, seq)
    return out


def find_heuristic_boundaries(text: str, langs: list[str] | None) -> list[Boundary]:
    """扫描文本并返回升序排列的候选边界，同偏移只保留优先级最高的那个。"""
    bounds: list[Boundary] = []

    # 换页符 —— 最强的单字符边界。
    for idx in all_rune_indices(text, "\f"):
        bounds.append(Boundary(rune_start=idx, priority=PRIO_FORM_FEED))

    # 逐行模式只扫一遍文本。
    lines = text.split("\n")
    chapter_patterns = chapter_patterns_for_langs(langs)
    pos = 0
    in_fence = False
    for i, line in enumerate(lines):
        trimmed = line.strip()
        if trimmed.startswith(FENCE):
            in_fence = not in_fence
        elif not in_fence:
            rune_start = pos
            added = False
            for pat in chapter_patterns:
                if pat.search(line):
                    bounds.append(Boundary(rune_start=rune_start, priority=PRIO_CHAPTER_MARKER))
                    added = True
                    break
            if not added and NumberedSectionPattern.search(line):
                bounds.append(Boundary(rune_start=rune_start, priority=PRIO_NUMBERED_HEAD))
                added = True
            if not added and AllCapsHeadingPattern.search(line):
                bounds.append(Boundary(rune_start=rune_start, priority=PRIO_ALL_CAPS_HEADING))
                added = True
            if not added and VisualSeparatorPattern.search(line):
                bounds.append(Boundary(rune_start=rune_start, priority=PRIO_VISUAL_SEP))
                added = True
            if not added and PageFooterPattern.search(line):
                bounds.append(Boundary(rune_start=rune_start, priority=PRIO_PAGE_FOOTER))
        pos += len(line)
        if i < len(lines) - 1:
            pos += 1  # \n

    # 连续空行块（\n{3,}）。取匹配的**结尾**作为边界，这样能干净地落进
    # 下一段。Go 那里是 utf8.RuneCountInString(text[:idx[1]])，即把字节
    # 下标换成 rune 下标，等价于 Python 的字符下标。
    for m in ExcessiveBlanksPattern.finditer(text):
        bounds.append(Boundary(rune_start=m.end(), priority=PRIO_BLANK_BLOCK))

    if not bounds:
        return []

    # 按位置排序；同位置只保留优先级最高的。
    bounds.sort(key=lambda b: (b.rune_start, -b.priority))
    deduped: list[Boundary] = []
    prev = -1
    for b in bounds:
        if b.rune_start != prev:
            deduped.append(b)
            prev = b.rune_start
    return deduped


def drop_bounds_inside_spans(bounds: list[Boundary], spans: list[Span]) -> list[Boundary]:
    """去掉严格落在某个受保护区间内部的边界。

    落在区间起点或终点的边界保留 —— 它们与保护区边缘对齐，不会切开
    受保护内容。spans 必须按 start 有序。
    """
    if not spans:
        return bounds
    out: list[Boundary] = []
    for b in bounds:
        inside = False
        for s in spans:
            if s.start >= b.rune_start:
                break  # 后面的 span 都在 b 之后，不可能包含 b
            if b.rune_start < s.end:
                inside = True
                break
        if not inside:
            out.append(b)
    return out


def all_rune_indices(text: str, needle: str) -> list[int]:
    """needle 在 text 中出现的所有 rune 偏移（只用于换页符这类单字符）。"""
    out: list[int] = []
    if needle == "":
        return out
    for pos, r in enumerate(text):
        if r == needle:
            out.append(pos)
    return out


def append_chunk(out: list[Chunk], runes: list[str], start: int, end: int, seq: int) -> int:
    """把 runes[start:end] 切片成一个 Chunk 追加到 out，返回新的 seq。

    Content 保存的是原始切片 —— Start/End 必须等于 Content 的 rune 长度，
    下游重建代码依赖这一点；给 embedding 用的空白裁剪在
    Chunk.EmbeddingContent 里做。纯空白切片会被跳过（边界聚集偶尔会
    产生这种切片）。
    """
    if end <= start:
        return seq
    raw = "".join(runes[start:end])
    if raw.strip() == "":
        return seq
    out.append(Chunk(content=raw, seq=seq, start=start, end=end))
    return seq + 1


def append_oversize_block(
    out: list[Chunk], runes: list[str], start: int, end: int, cfg: SplitterConfig, seq: int
) -> int:
    """把本身超过 chunk_size 的区域递归切分，仍然复用 legacy 切分器以继承
    其长度预算与保护区模式。"""
    if end <= start:
        return seq
    sub_text = "".join(runes[start:end])
    subs = split_text(sub_text, cfg)
    for s in subs:
        out.append(
            Chunk(content=s.content, seq=seq, start=start + s.start, end=start + s.end)
        )
        seq += 1
    return seq


def apply_overlap_aligned(runes: list[str], cur_end: int, overlap: int, bounds: list[Boundary]) -> int:
    """返回下一个 chunk 的起点偏移。

    目标是 curEnd - overlap，但会吸附到最近的前置边界（2x overlap 以内），
    否则退回到上一个换行处，避免切在行/词中间。两者都不可用时才用原始
    目标值。

    curEnd 本身必然是某个边界（装箱器总在边界处 flush），因此搜索时排除它
    —— 选它会导致零重叠，反而失去这个函数的意义。
    """
    if overlap <= 0:
        return cur_end
    target = cur_end - overlap
    if target < 0:
        target = 0
    # 允许的搜索窗口：[curEnd - 2*overlap, curEnd)
    window_start = cur_end - 2 * overlap
    if window_start < 0:
        window_start = 0

    # 优先选窗口内的语义边界。
    best_bound = -1
    for b in bounds:
        if window_start <= b.rune_start < cur_end and b.rune_start > best_bound:
            best_bound = b.rune_start
    if best_bound >= 0:
        return best_bound

    # 退化方案：从 target 往回找上一个换行，但不越出 windowStart，
    # 这样重叠量级不会失控。
    if target < len(runes):
        i = target
        while i > window_start:
            if runes[i] == "\n":
                return i + 1
            i -= 1
    return target


# Go 导出名别名
SplitByHeuristicsImpl = split_by_heuristics_impl
FindHeuristicBoundaries = find_heuristic_boundaries
DropBoundsInsideSpans = drop_bounds_inside_spans
ApplyOverlapAligned = apply_overlap_aligned
