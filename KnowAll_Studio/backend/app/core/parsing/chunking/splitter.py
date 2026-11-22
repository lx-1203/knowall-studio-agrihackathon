"""递归文本切分 + 保护区（legacy Tier 3）。

移植自 Python docreader/splitter/splitter.py 的递归切分器。核心思想：
1) 先用正则找出“不能被切开”的区域（公式、表格、代码块、链接、行内代码）；
2) 其余部分按分隔符优先级递归切分；
3) 最后按 chunk_size 合并单元，并按语义边界保留 overlap。

长度单位在本模块里是混用的，必须逐处区分：
- SplitUnit/Chunk 的 start/end 一律是 rune（= Python 的字符）偏移；
- protected_spans 返回的是 UTF-8 **字节**偏移（Go 的 regexp 给的就是字节），
  build_units_with_protection 里的 byte_pos 也是字节，只有经过
  protected_spans_rune 转换后才是 rune 偏移。
Go 的 len() 是字节长度，Python 的 len() 是字符长度，所以凡是字节语义的地方
都显式写成 len(s.encode("utf-8"))（见 byte_len）。
"""

from __future__ import annotations

import re
from bisect import bisect_left

from .header_tracker import HeaderTracker, header_column_mismatch
from .types import (
    ABSOLUTE_MAX_SIZE,
    MAX_PROTECTED_SIZE,
    ChildChunk,
    Chunk,
    ParentChildResult,
    Span,
    SplitUnit,
    SplitterConfig,
)

# 不能被切开的区域。Markdown 链接/图片模式刻意排除 \n 并限制文本/目标长度：
# CommonMark 禁止它们跨空行，而之前无界的 [^\]]* / [^)]+ 会让 OCR 残留的
# 一个 '[' 把整段吞成一个"受保护"的原子区，彻底废掉分块。
protected_patterns: list[re.Pattern[str]] = [
    re.compile(r"\$\$.*?\$\$", re.S | re.ASCII),  # LaTeX 块级公式
    re.compile(r"!\[[^\]\n]{0,200}\]\([^)\n]{1,500}\)", re.ASCII),
    re.compile(r"\[[^\]\n]{1,200}\]\([^)\n]{1,500}\)", re.ASCII),
    # 表头 + 分隔行
    re.compile(r"[ ]*(?:\|[^|\n]*)+\|[\r\n]+\s*(?:\|\s*:?-{3,}:?\s*)+\|?[\r\n]+", re.M | re.ASCII),
    # 表格数据行
    re.compile(r"[ ]*(?:\|[^|\n]*)+\|[\r\n]+", re.M | re.ASCII),
    # 围栏代码块
    re.compile("```(?:\\w+)?[\\r\\n].*?```", re.S | re.ASCII),
    # Markdown 行内代码
    re.compile("`[^`\\r\\n]+`", re.ASCII),
]

# 最长语义分隔符（\r\n\r\n）的 rune 长度：重叠边界允许回看这么多字符，
# 否则刚好被窗口切开的 \r\n\r\n 就看不到了。
SEMANTIC_OVERLAP_LOOKBEHIND = 4


def rune_len(s: str) -> int:
    """Go 的 utf8.RuneCountInString：Python 的字符数就是 rune 数。"""
    return len(s)


def byte_len(s: str) -> int:
    """Go 的 len(s)：UTF-8 **字节**长度（与 rune_len 不同，勿混用）。"""
    return len(s.encode("utf-8"))


def _byte_offsets(text: str) -> list[int]:
    """offs[i] = text[:i] 的 UTF-8 字节长度，用于字节偏移 <-> 字符下标换算。"""
    offs = [0] * (len(text) + 1)
    total = 0
    for i, ch in enumerate(text):
        total += len(ch.encode("utf-8"))
        offs[i + 1] = total
    return offs


def _char_index_for_byte(byte_offsets: list[int], byte_pos: int) -> int:
    """字节偏移 -> 字符下标（保护区边界一定落在字符边界上）。"""
    return bisect_left(byte_offsets, byte_pos)


def protected_spans(text: str) -> list[Span]:
    """找出所有互不重叠的受保护区域。

    返回的 start/end 是 **UTF-8 字节偏移**，与 Go 保持一致；需要 rune 偏移
    的调用方请走 protected_spans_rune。
    """
    offs = _byte_offsets(text)
    all_spans: list[tuple[int, int]] = []
    for pat in protected_patterns:
        for m in pat.finditer(text):
            if m.end() > m.start():
                all_spans.append((offs[m.start()], offs[m.end()]))
    if not all_spans:
        return []

    # 按起点升序、长度降序排序（Go 用的是等价的插入排序，同为稳定排序）
    all_spans.sort(key=lambda m: (m[0], -(m[1] - m[0])))

    # 去掉重叠：保留起点 >= 上一个已保留区间终点的那些
    result: list[Span] = []
    last_end = 0
    for start, end in all_spans:
        if start >= last_end:
            result.append(Span(start, end))
            last_end = end
    return result


def protected_spans_rune(text: str, byte_spans: list[Span]) -> list[Span]:
    """把字节偏移的保护区转成 rune 偏移，单趟前向扫描。

    给在 rune 空间工作的调用方（启发式分块器、语义重叠边界）使用，
    避免切在保护区内部。byte_spans 必须按起点有序（protected_spans 保证）。
    """
    if not byte_spans:
        return []
    offs = _byte_offsets(text)
    n = len(text)
    out: list[Span] = []
    ci = 0
    for s in byte_spans:
        while ci < n and offs[ci] < s.start:
            ci += 1
        start_rune = ci
        while ci < n and offs[ci] < s.end:
            ci += 1
        out.append(Span(start_rune, ci))
    return out


def split_by_separators(text: str, separators: list[str], chunk_size: int) -> list[str]:
    """按分隔符优先级切分，仍然超大的片段继续用更低优先级的分隔符递归切。

    与 Python 参考实现（docreader/splitter/splitter.py:_split）的递归优先级
    语义一致：如果 \n\n 切出来的某一片仍然太大，那么 \n（以及后续分隔符）
    只作用在那一片上，而不是整个文本。

    chunk_size == 0 时关闭递归保护，供不关心尺寸预算的调用方使用。
    """
    if text == "" or not separators:
        return [text]
    if chunk_size > 0 and rune_len(text) <= chunk_size:
        return [text]

    for i, sep in enumerate(separators):
        if sep == "":
            continue
        pat = re.compile("(" + re.escape(sep) + ")", re.ASCII)
        # 注意：Go 的 re.Split 返回的是"匹配之间的文本"，不包含捕获组内容；
        # Python 的 re.split 会把捕获组一起返回，两者语义不同。这里照 Go 的
        # 语义手工切分，否则分隔符会被重复追加。
        splits: list[str] = []
        matches: list[str] = []
        beg = 0
        for m in pat.finditer(text):
            splits.append(text[beg : m.start()])
            matches.append(m.group(0))
            beg = m.end()
        splits.append(text[beg:])
        if not matches:
            continue

        pieces: list[str] = []
        for j, s in enumerate(splits):
            if s != "":
                pieces.append(s)
            if j < len(matches) and matches[j] != "":
                pieces.append(matches[j])
        if len(pieces) <= 1:
            continue

        # 仍然过大的片段交给剩余（更低优先级）的分隔符继续递归。
        out: list[str] = []
        remaining = separators[i + 1 :]
        for p in pieces:
            if chunk_size > 0 and rune_len(p) > chunk_size and remaining:
                out.extend(split_by_separators(p, remaining, chunk_size))
            else:
                out.append(p)
        return out
    return [text]


def split_text(text: str, cfg: SplitterConfig) -> list[Chunk]:
    """按分隔符切分并带 overlap，同时尊重保护区（legacy 入口）。"""
    if text == "":
        return []

    chunk_size = cfg.chunk_size
    chunk_overlap = cfg.chunk_overlap
    separators = cfg.separators

    if chunk_size <= 0:
        chunk_size = 512
    if chunk_overlap < 0:
        chunk_overlap = 0

    # 步骤 1：找出保护区
    protected = protected_spans(text)

    # 步骤 2：非保护区按分隔符切分，保护区作为原子单元保留。
    # chunk_size 会传下去，让 splitBySeparators 能对超大片段递归应用更低
    # 优先级的分隔符（与 Python 参考实现一致）。
    units = build_units_with_protection(text, protected, separators, chunk_size)

    # 步骤 3：把单元合并成带 overlap 的 chunk
    return merge_units(units, chunk_size, chunk_overlap)


def build_units_with_protection(
    text: str, protected: list[Span], separators: list[str], chunk_size: int
) -> list[SplitUnit]:
    """把文本切成单元，保护区保持原子性。

    返回单元里的 start/end 是 rune 偏移（不是字节偏移），因为下游合并逻辑
    是按 rune 切片索引的。无分隔符的长文本和超过尺寸预算的保护区也会
    被强制切开，避免产出下游（embedding API）无法处理的超大 chunk。
    """
    units: list[SplitUnit] = []
    offs = _byte_offsets(text)
    total_bytes = offs[-1]
    byte_pos = 0
    rune_pos = 0

    def append_unprotected(parts: list[str], start: int) -> None:
        """Split a final separator-free unit before it reaches merge_units."""
        position = start
        for part in parts:
            step = chunk_size if chunk_size > 0 else len(part) or 1
            for offset in range(0, len(part), step):
                piece = part[offset:offset + step]
                units.append(SplitUnit(
                    text=piece, start=position + offset,
                    end=position + offset + len(piece),
                ))
            position += len(part)

    for p in protected:
        if p.start > byte_pos:
            pre = text[_char_index_for_byte(offs, byte_pos) : _char_index_for_byte(offs, p.start)]
            parts = split_by_separators(pre, separators, chunk_size)
            append_unprotected(parts, rune_pos)
            rune_pos += rune_len(pre)

        prot_text = text[_char_index_for_byte(offs, p.start) : _char_index_for_byte(offs, p.end)]
        prot_rune_len = rune_len(prot_text)

        protected_limit = min(MAX_PROTECTED_SIZE, chunk_size) if chunk_size > 0 else MAX_PROTECTED_SIZE
        if prot_rune_len > protected_limit:
            # Protection cannot make a block too large for the embedding budget.
            # Prefer nearby whitespace; split at the limit when none exists.
            runes = list(prot_text)
            offset = 0
            while offset < len(runes):
                chunk_end = offset + protected_limit
                if chunk_end > len(runes):
                    chunk_end = len(runes)
                else:
                    i = chunk_end - 1
                    while i > offset and i > chunk_end - 200:
                        if runes[i] == "\n" or runes[i] == " ":
                            chunk_end = i + 1
                            break
                        i -= 1
                units.append(
                    SplitUnit(
                        text="".join(runes[offset:chunk_end]),
                        start=rune_pos + offset,
                        end=rune_pos + chunk_end,
                    )
                )
                offset = chunk_end
        else:
            # 常规情况：保护区整体作为一个单元
            units.append(SplitUnit(text=prot_text, start=rune_pos, end=rune_pos + prot_rune_len))

        rune_pos += prot_rune_len
        byte_pos = p.end

    if byte_pos < total_bytes:
        remaining = text[_char_index_for_byte(offs, byte_pos) :]
        parts = split_by_separators(remaining, separators, chunk_size)
        append_unprotected(parts, rune_pos)

    return units


def merge_units(units: list[SplitUnit], chunk_size: int, chunk_overlap: int) -> list[Chunk]:
    """把切分单元合并成 chunk，并维护 overlap 与上下文表头。

    同时强制一个绝对上限，避免产出超过下游（embedding API）限制的 chunk。
    活跃的上下文表头（如 Markdown 表头）会被前置到新 chunk 上，让每个
    chunk 自带表头语义。
    """
    if not units:
        return []

    ht = HeaderTracker()
    chunks: list[Chunk] = []
    current: list[SplitUnit] = []
    cur_len = 0

    for u in units:
        u_len = rune_len(u.text)

        # 单个单元就超过绝对上限时，强制再切
        if u_len > ABSOLUTE_MAX_SIZE:
            if current:
                chunks.append(build_chunk(current, len(chunks)))
                current = []
                cur_len = 0

            # 超大单元也要更新表头状态
            ht.update(u.text)

            runes = list(u.text)
            offset = 0
            while offset < len(runes):
                chunk_end = offset + ABSOLUTE_MAX_SIZE
                if chunk_end > len(runes):
                    chunk_end = len(runes)
                else:
                    i = chunk_end - 1
                    while i > offset and i > chunk_end - 200:
                        if runes[i] == "\n" or runes[i] == " ":
                            chunk_end = i + 1
                            break
                        i -= 1
                chunks.append(
                    Chunk(
                        content="".join(runes[offset:chunk_end]),
                        seq=len(chunks),
                        start=u.start + offset,
                        end=u.start + chunk_end,
                    )
                )
                offset = chunk_end
            continue

        ht.update(u.text)
        # 表格边界处先 flush，免得下一个表格被并进仍然带着上一个表格表头
        # 上下文的 chunk 里。
        if ht.header_ended_this_unit and current:
            chunks.append(build_chunk(current, len(chunks)))
            current = []
            cur_len = 0
        headers = ht.get_headers()
        headers_len = rune_len(headers)
        if headers_len > chunk_size:
            headers = ""
            headers_len = 0

        # 加上这个单元（并为下一个 chunk 预留表头空间）会超预算时就 flush。
        if cur_len + u_len + headers_len > chunk_size and current:
            chunks.append(build_chunk(current, len(chunks)))

            # 从 current 尾部保留重叠
            current, cur_len = compute_overlap(current, chunk_overlap, chunk_size, u_len)

            # 还需要为"表头 + 下一个单元"腾地方时继续缩小重叠
            if headers != "" and headers_len + u_len <= chunk_size:
                while current and cur_len + u_len + headers_len > chunk_size:
                    cur_len -= rune_len(current[0].text)
                    current = current[1:]

                # 列名上下文已经出现在重叠或下一个单元里时不再前置
                overlap_text = units_text(current)
                if not header_already_present(headers, overlap_text, u.text) and not header_column_mismatch(
                    headers, u.text
                ):
                    start_pos = u.start
                    if current:
                        start_pos = current[0].start
                    h_unit = SplitUnit(text=headers, start=start_pos, end=start_pos)
                    current = [h_unit] + current
                    cur_len += headers_len

        # 加上这个单元是否会超过绝对上限
        if cur_len + u_len > ABSOLUTE_MAX_SIZE:
            if current:
                chunks.append(build_chunk(current, len(chunks)))
                current = []
                cur_len = 0

        current.append(u)
        cur_len += u_len

    if current:
        chunks.append(build_chunk(current, len(chunks)))

    return chunks


def units_text(units: list[SplitUnit]) -> str:
    return "".join(u.text for u in units)


def header_already_present(headers: str, overlap_text: str, unit_text: str) -> bool:
    """表头的列名行是否已经出现在重叠或下一个单元里（避免重复前置）。"""
    if headers in overlap_text or headers in unit_text:
        return True

    # 取出列名行（第一行非分隔行的有效内容）。对于被重写过的表头
    # "| col1 | col2 |\n| --- | --- |\n"，第一行就是列名。
    col_row = header_column_row(headers)
    if col_row == "":
        return False

    return col_row in overlap_text or col_row in unit_text


def header_column_row(header: str) -> str:
    """从表头字符串中取出列名行；没有有效列名时返回空串。"""
    for raw in header.split("\n"):
        line = raw.strip()
        if line == "" or "---" in line:
            continue
        # 全是竖线/空白的行（空表头行）跳过
        if not all(c in "| \t" for c in line):
            return line
    return ""


def build_chunk(units: list[SplitUnit], seq: int) -> Chunk:
    return Chunk(
        content=units_text(units),
        seq=seq,
        start=units[0].start,
        end=units[-1].end,
    )


def compute_overlap(
    current: list[SplitUnit], chunk_overlap: int, chunk_size: int, next_len: int
) -> tuple[list[SplitUnit], int]:
    """返回要保留的语义后缀及其 rune 长度。

    配置的 overlap 是硬上界，而不是直接按字符切片。边界探测最多会多看
    overlap 窗口前面 4 个 rune，这样被窗口切断的分隔符仍然可见。一个候选
    边界只有在其结束位置 >= 原始窗口起点时才合格，因此保留内容绝不会超过
    overlap 上限。合格边界的优先级：

      1. 段落分隔（\n\n / \r\n\r\n）
      2. 换行（\n / \r\n）
      3. 句末（。？！、". "、" ? "、"! "）

    先比优先级；同优先级时取窗口中最早的，好让重叠尽可能大。下一个 chunk
    从选中的分隔符之后开始。窗口里没有合法语义边界时就不保留重叠 ——
    这样重叠是有界且确定的，而不是退化成整单元拼接或直接归零。
    """
    if chunk_overlap <= 0:
        return [], 0

    # 重叠也算下一个 chunk 的体积：来料单元已经接近预算时缩小搜索窗口，
    # 而不是产出超标的 chunk。
    max_overlap = chunk_overlap
    remaining = chunk_size - next_len
    if remaining < max_overlap:
        max_overlap = remaining
    if max_overlap <= 0:
        return [], 0

    window = semantic_overlap_window(current, max_overlap + SEMANTIC_OVERLAP_LOOKBEHIND)
    if not window:
        return [], 0

    window_text = units_text(window)
    # 原始窗口起点之前的坐标属于回看区。偏移是左闭右开，所以要求
    # boundaryEnd >= originalWindowStart 等价于要求分隔符最后一个 rune
    # 落在 -1 或其之后。
    original_window_start = rune_len(window_text) - max_overlap
    if original_window_start < 0:
        original_window_start = 0
    boundary_end, ok = find_semantic_overlap_boundary_ending_at_or_after(
        window_text, original_window_start
    )
    if not ok:
        return [], 0

    overlap = trim_units_prefix(window, boundary_end)
    overlap_len = sum(rune_len(u.text) for u in overlap)
    if overlap_len <= 0 or overlap_len > max_overlap or units_text(overlap).strip() == "":
        return [], 0
    return overlap, overlap_len


def semantic_overlap_window(current: list[SplitUnit], max_len: int) -> list[SplitUnit]:
    """从 current 尾部取至多 max_len 个有原文来源的 rune。

    可以在第一个保留单元内部切开，这样大段落内部的语义边界依然可被发现。
    合成的零宽单元（例如重复前置的表头）是硬边界：跨越它们会破坏
    Start/End 与 Content 的来源一致不变式。
    """
    if max_len <= 0 or not current:
        return []

    remaining = max_len
    reversed_units: list[SplitUnit] = []
    for i in range(len(current) - 1, -1, -1):
        if remaining <= 0:
            break
        u = current[i]
        u_len = rune_len(u.text)
        if u_len == 0:
            continue
        # 表头标记是生成文本、不占原文区间：推导重叠后缀时不得包含或跨越它。
        if u.start == u.end or u.end - u.start != u_len:
            break

        if u_len <= remaining:
            reversed_units.append(u)
            remaining -= u_len
            continue

        runes = list(u.text)
        start = u_len - remaining
        reversed_units.append(
            SplitUnit(text="".join(runes[start:]), start=u.start + start, end=u.end)
        )
        remaining = 0

    if not reversed_units:
        return []
    return list(reversed(reversed_units))


def find_semantic_overlap_boundary(text: str) -> tuple[int, bool]:
    """返回选中分隔符之后的 rune 偏移。"""
    return find_semantic_overlap_boundary_ending_at_or_after(text, 0)


def find_semantic_overlap_boundary_ending_at_or_after(text: str, min_end: int) -> tuple[int, bool]:
    """只对结束偏移 >= min_end 的候选应用常规的优先级/最早位置规则。

    先过滤再比较，避免一个更早但不合格的落后分隔符遮住后面合格的那个。
    保护区（Markdown/代码/公式）内部的边界一律忽略，只有空白跟在后面的
    边界也不算数。
    """
    runes = list(text)
    if not runes:
        return 0, False
    if min_end < 0:
        min_end = 0
    if min_end > len(runes):
        return 0, False

    protected = protected_spans_rune(text, protected_spans(text))

    def inside_protected(pos: int) -> bool:
        for p in protected:
            if pos < p.start:
                return False
            if p.start <= pos < p.end:
                return True
        return False

    def has_meaningful_tail(end: int) -> bool:
        return 0 <= end < len(runes) and "".join(runes[end:]).strip() != ""

    best_priority = 0
    best_start = 0
    best_end = 0
    found = False

    def consider(start: int, end: int, priority: int) -> None:
        nonlocal best_priority, best_start, best_end, found
        if (
            start < 0
            or end <= start
            or end < min_end
            or end > len(runes)
            or inside_protected(start)
            or not has_meaningful_tail(end)
        ):
            return
        if (
            not found
            or priority < best_priority
            or (priority == best_priority and start < best_start)
        ):
            best_priority = priority
            best_start = start
            best_end = end
            found = True

    # 先标记段落分隔占用的 rune，防止其中的换行又被当成低优先级的换行候选。
    paragraph_rune = [False] * len(runes)
    i = 0
    while i < len(runes):
        if (
            i + 3 < len(runes)
            and runes[i] == "\r"
            and runes[i + 1] == "\n"
            and runes[i + 2] == "\r"
            and runes[i + 3] == "\n"
        ):
            consider(i, i + 4, 1)
            for j in range(i, i + 4):
                paragraph_rune[j] = True
            i += 4
            continue
        if i + 1 < len(runes) and runes[i] == "\n" and runes[i + 1] == "\n":
            consider(i, i + 2, 1)
            paragraph_rune[i] = True
            paragraph_rune[i + 1] = True
            i += 2
            continue
        i += 1

    i = 0
    while i < len(runes):
        if not paragraph_rune[i]:
            if runes[i] == "\r" and i + 1 < len(runes) and runes[i + 1] == "\n" and not paragraph_rune[i + 1]:
                consider(i, i + 2, 2)
                i += 2
                continue
            if runes[i] == "\n":
                consider(i, i + 1, 2)
        i += 1

    for i, r in enumerate(runes):
        if r in ("。", "？", "！"):
            consider(i, i + 1, 3)
        elif r in (".", "?", "!"):
            if i + 1 < len(runes) and runes[i + 1] == " ":
                consider(i, i + 2, 3)

    if not found:
        return 0, False
    return best_end, True


def trim_units_prefix(units: list[SplitUnit], prefix_len: int) -> list[SplitUnit]:
    """去掉前 prefix_len 个 rune，同时保留剩余单元的原始位置。

    prefix_len 是相对 unitsText 的偏移。
    """
    if prefix_len <= 0:
        return list(units)

    remaining = prefix_len
    out: list[SplitUnit] = []
    for u in units:
        u_len = rune_len(u.text)
        if remaining >= u_len:
            remaining -= u_len
            continue
        if remaining > 0:
            runes = list(u.text)
            u = SplitUnit(text="".join(runes[remaining:]), start=u.start + remaining, end=u.end)
            remaining = 0
        out.append(u)
    return out


def split_text_parent_child(text: str, parent_cfg: SplitterConfig, child_cfg: SplitterConfig) -> ParentChildResult:
    """两级分块（legacy 版本，不走策略链）：

    1. 先用 parent_cfg 切出大块父块。
    2. 每个父块再用 child_cfg 切成小块子块用于 embedding。

    子块的 Seq 在整个文档内全局唯一。
    """
    parents = split_text(text, parent_cfg)
    if not parents:
        return ParentChildResult()

    new_parents: list[Chunk] = []
    children: list[ChildChunk] = []
    child_seq = 0
    for parent in parents:
        subs = split_text(parent.content, child_cfg)

        parent_index = -1
        if len(subs) > 1 or (len(subs) == 1 and subs[0].content != parent.content):
            parent_index = len(new_parents)
            new_parents.append(parent)

        for sub in subs:
            # 偏移修正：子块位置是相对父块内容的，要平移到文档级偏移。
            # 用加法平移（而不是按 Content 长度）才能在带上下文表头的块上
            # 保持正确的位置追踪。
            sub.seq = child_seq
            sub.start += parent.start
            sub.end += parent.start
            children.append(ChildChunk(
                content=sub.content,
                context_header=sub.context_header,
                seq=sub.seq,
                start=sub.start,
                end=sub.end,
                parent_index=parent_index,
            ))
            child_seq += 1
    return ParentChildResult(parents=new_parents, children=children)


# Go 导出名别名
SplitText = split_text
SplitTextParentChild = split_text_parent_child
SplitBySeparators = split_by_separators
BuildUnitsWithProtection = build_units_with_protection
MergeUnits = merge_units
ProtectedSpans = protected_spans
ProtectedSpansRune = protected_spans_rune
ComputeOverlap = compute_overlap
FindSemanticOverlapBoundary = find_semantic_overlap_boundary
FindSemanticOverlapBoundaryEndingAtOrAfter = find_semantic_overlap_boundary_ending_at_or_after
