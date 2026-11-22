"""Tier 1：Markdown 标题感知分块。

有规范标题结构的文档按标题边界切开，并给每个 chunk 前置当前标题栈的
面包屑（例如 # Chapter 1 / ## Section 1.2 两行）。面包屑放在
Chunk.ContextHeader 而不是 Content 里，是为了保住
End-Start == len(Content) 这条文档重建依赖的不变式。
"""

from __future__ import annotations

import copy

from .heading_hierarchy import HeadingHierarchy
from .patterns import MarkdownHeadingPattern
from .profiler import FENCE, profile_document
from .splitter import rune_len, split_text
from .types import (
    Chunk,
    DocProfile,
    HeadingBoundary,
    SectionBreadcrumb,
    SplitterConfig,
)


def split_by_headings_impl(text: str, cfg: SplitterConfig, profile: DocProfile | None = None) -> list[Chunk]:
    """Tier-1 实现。

    文档没有可用的标题结构、或按标题切完只有一个小节时，回落到 legacy 切分器。

    profile 允许为 None，此时就地计算；auto 策略已经把画像算好了，
    从这里透传进来可以避免重复扫描整篇文档。
    """
    if text == "":
        return []
    if profile is None:
        profile = profile_document(text)
    primary_level = profile.dominant_heading_level()
    if primary_level == 0:
        return split_text(text, cfg)

    bounds = find_heading_boundaries(text, primary_level)
    if len(bounds) <= 1:
        return split_text(text, cfg)

    runes = list(text)
    hierarchy = HeadingHierarchy()

    # 预先遍历所有标题（不只是主导层级），这样每个小节起点的层级栈都是
    # 完整嵌套的。面包屑只在小节边界处快照；小节内部的深层子标题会更新
    # 层级栈，但不改变该 chunk 的面包屑（同一小节内的 chunk 共享面包屑）。
    out: list[Chunk] = []
    seq = 0

    for i, b in enumerate(bounds):
        end_rune = len(runes)
        if i + 1 < len(bounds):
            end_rune = bounds[i + 1].rune_start
        if b.line != "":
            hierarchy.observe(b.line)
        # 把主导边界与下一条边界之间的子标题也喂进去，让后续小节看到的
        # 层级栈保持同步。这里刻意放在 observe 小节标题之后执行，使面包屑
        # 反映的是小节自身的标题。
        breadcrumb = hierarchy.breadcrumb_with_hashes()
        section_start = hierarchy.copy()
        observe_sub_headings("".join(runes[b.rune_start : end_rune]), primary_level, hierarchy)

        section_runes = runes[b.rune_start : end_rune]
        section_content = "".join(section_runes)
        sec_len = len(section_runes)
        if sec_len == 0:
            continue

        bc_len = rune_len(breadcrumb)
        # 小节本身就装得下：原样产出，面包屑另行保存。
        if bc_len + 2 + sec_len <= cfg.chunk_size:
            out.append(
                Chunk(
                    content=section_content,
                    context_header=breadcrumb,
                    seq=seq,
                    start=b.rune_start,
                    end=end_rune,
                )
            )
            seq += 1
            continue

        # 小节太大：内部再交给 legacy 切分器。这里不需要为面包屑压缩
        # ChunkSize 预算，因为面包屑本来就不计入 Content。每个子块拿到的是
        # 自己起点处最深的标题路径，长小节里的 ###/#### 子标题不会被塌缩成
        # 小节标题。
        sub_breadcrumbs = section_breadcrumbs(section_runes, primary_level, section_start)
        sub_chunks = split_text(section_content, cfg)
        for sub in sub_chunks:
            out.append(
                Chunk(
                    content=sub.content,
                    context_header=breadcrumb_at_offset(sub_breadcrumbs, sub.start, breadcrumb),
                    seq=seq,
                    start=b.rune_start + sub.start,
                    end=b.rune_start + sub.end,
                )
            )
            seq += 1

    return coalesce_tiny_chunks(out, cfg.chunk_size)


def coalesce_tiny_chunks(inp: list[Chunk], chunk_size: int) -> list[Chunk]:
    """把小而相邻、且共享标题上下文的 chunk 合并。

    FAQ、安装日志、变更清单这类文档的主导小节大多很短，不合并就会触发
    validator 的 "too many tiny chunks" 并一路降级到 legacy。合并后的面包屑
    取两者的公共行前缀；原来的子标题仍然可见，因为 heading_splitter 会把
    标题行留在各小节的 Content 里。

    安全性：
    - 只在 cur.End == next.Start 时合并，从而保住文档重建依赖的
      End-Start == len([]rune(Content)) 不变式，并天然跳过 legacy 子块
      （它们可能因 ChunkOverlap 而重叠）。
    - 累积到达合并目标（约 ChunkSize/2）就停止，不会比 validator 认为
      舒适的程度更激进地打包。
    """
    if len(inp) <= 1 or chunk_size <= 0:
        return inp
    target = chunk_size // 2
    if target < 200:
        target = 200

    out: list[Chunk] = []
    # Go 里 cur 是结构体副本，这里显式复制，避免就地改写调用方的对象。
    cur = copy.copy(inp[0])
    cur_len = rune_len(cur.content)

    for i in range(1, len(inp)):
        nxt = inp[i]
        next_len = rune_len(nxt.content)
        shared_header = common_heading_prefix(cur.context_header, nxt.context_header)
        # 相邻 + 仍然偏小 + 不超预算 → 合并
        if (
            shared_header != ""
            and cur.end == nxt.start
            and cur_len < target
            and cur_len + next_len <= chunk_size
        ):
            cur.content += nxt.content
            cur.context_header = shared_header
            cur.end = nxt.end
            cur_len += next_len
            continue
        out.append(cur)
        cur = copy.copy(nxt)
        cur_len = next_len
    out.append(cur)

    # 重新编号：下游要求 Seq 是稠密的 0..N-1。
    for i in range(len(out)):
        out[i].seq = i
    return out


def common_heading_prefix(a: str, b: str) -> str:
    """两个面包屑字符串的最长行对齐公共前缀。

    标题层级输出形如 "# Top\n## Section\n### Sub"，逐行比较即可，
    也避免按字符截断造成面包屑损坏。
    """
    if a == b:
        return a
    la = a.split("\n")
    lb = b.split("\n")
    n = min(len(la), len(lb))
    common = 0
    for i in range(n):
        if la[i] != lb[i]:
            break
        common = i + 1
    if common == 0:
        return ""
    return "\n".join(la[:common])


def find_heading_boundaries(text: str, primary_level: int) -> list[HeadingBoundary]:
    """返回偏移 0 处的边界，加上每个层级 <= primaryLevel 且不在围栏代码块内的标题。

    标题识别是行导向的：标题必须独占一行才会被识别。
    """
    runes = list(text)
    bounds: list[HeadingBoundary] = [HeadingBoundary(rune_start=0)]
    if not runes:
        return bounds

    pos = 0
    in_fence = False
    lines = text.split("\n")
    for i, line in enumerate(lines):
        trimmed = line.strip()
        if trimmed.startswith(FENCE):
            in_fence = not in_fence
            pos += rune_len(line)
            if i < len(lines) - 1:
                pos += 1  # 换行
            continue
        if not in_fence:
            m = MarkdownHeadingPattern.search(line)
            if m is not None:
                level = len(m.group(1))
                if 1 <= level <= primary_level and pos > 0:
                    bounds.append(HeadingBoundary(rune_start=pos, line=line))
                if 1 <= level <= primary_level and pos == 0:
                    # 第一行就是标题 —— 替换掉开头的隐含边界
                    bounds[0].line = line
        pos += rune_len(line)
        if i < len(lines) - 1:
            pos += 1  # 补回 split 吃掉的换行
    return bounds


def observe_sub_headings(section: str, primary_level: int, h: HeadingHierarchy) -> None:
    """遍历小节各行，把比 primaryLevel 更深的标题喂给层级栈。

    这样下一个主导小节处的面包屑才反映真正活跃的标题栈。
    """
    if section == "":
        return
    in_fence = False
    for line in section.split("\n"):
        trimmed = line.strip()
        if trimmed.startswith(FENCE):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = MarkdownHeadingPattern.search(line)
        if m is None:
            continue
        level = len(m.group(1))
        if level > primary_level:
            h.observe(line)


def section_breadcrumbs(
    section_runes: list[str], primary_level: int, seed: HeadingHierarchy
) -> list[SectionBreadcrumb]:
    """记录小节内部每个更深子标题生效处的 rune 偏移与随之而来的面包屑。

    seed 是小节起点处的层级栈状态（已包含小节标题及其祖先）。返回列表按
    rune_start 有序，且总是以偏移 0 处的 seed 面包屑开头，因此一个深处
    子标题之后的子块也能解析到该标题的路径，而不是小节级标题。
    """
    h = seed.copy()
    result: list[SectionBreadcrumb] = [
        SectionBreadcrumb(rune_start=0, breadcrumb=h.breadcrumb_with_hashes())
    ]
    pos = 0
    in_fence = False
    lines = "".join(section_runes).split("\n")
    for i, line in enumerate(lines):
        trimmed = line.strip()
        if trimmed.startswith(FENCE):
            in_fence = not in_fence
            pos += rune_len(line)
            if i < len(lines) - 1:
                pos += 1
            continue
        if not in_fence:
            m = MarkdownHeadingPattern.search(line)
            if m is not None and len(m.group(1)) > primary_level:
                h.observe(line)
                result.append(
                    SectionBreadcrumb(rune_start=pos, breadcrumb=h.breadcrumb_with_hashes())
                )
        pos += rune_len(line)
        if i < len(lines) - 1:
            pos += 1
    return result


def breadcrumb_at_offset(bcs: list[SectionBreadcrumb], offset: int, fallback: str) -> str:
    """返回给定 rune 偏移处生效的面包屑 —— runeStart <= offset 的最后一条。"""
    bc = fallback
    for e in bcs:
        if e.rune_start > offset:
            break
        bc = e.breadcrumb
    return bc


# Go 导出名别名
SplitByHeadingsImpl = split_by_headings_impl
CoalesceTinyChunks = coalesce_tiny_chunks
CommonHeadingPrefix = common_heading_prefix
FindHeadingBoundaries = find_heading_boundaries
ObserveSubHeadings = observe_sub_headings
SectionBreadcrumbs = section_breadcrumbs
BreadcrumbAtOffset = breadcrumb_at_offset
