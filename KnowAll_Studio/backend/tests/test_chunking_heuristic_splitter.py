"""heuristic_splitter_test.go 的逐条翻译。"""

from __future__ import annotations

from app.core.parsing.chunking import (
    LANG_CHINESE,
    Chunk,
    SplitterConfig,
    default_config,
    drop_bounds_inside_spans,
    find_heuristic_boundaries,
    protected_spans,
    protected_spans_rune,
    split_by_heuristics_impl,
)


def chunk_lengths(chunks: list[Chunk]) -> list[int]:
    return [len(c.content) for c in chunks]


def test_split_by_heuristics_form_feed_boundary() -> None:
    doc = "page one body text. " * 30 + "\f" + "page two body. " * 30
    cfg = SplitterConfig(chunk_size=400, chunk_overlap=20, separators=[". "])
    chunks = split_by_heuristics_impl(doc, cfg, None)
    assert len(chunks) >= 2


def test_split_by_heuristics_numbered_sections() -> None:
    body = "body sentence. " * 8
    doc = "1. Introduction\n" + body + "\n\n2. Methods\n" + body + "\n\n3. Results\n" + body
    cfg = SplitterConfig(chunk_size=200, chunk_overlap=20, separators=[". "])
    chunks = split_by_heuristics_impl(doc, cfg, None)
    assert len(chunks) >= 2


def test_split_by_heuristics_german_chapter_markers() -> None:
    body = "Beispieltext. " * 10
    doc = "Kapitel 1: Einführung\n" + body + "\n\nKapitel 2: Hauptteil\n" + body
    cfg = SplitterConfig(chunk_size=200, chunk_overlap=20, separators=[". "])
    chunks = split_by_heuristics_impl(doc, cfg, None)
    assert len(chunks) >= 2


def test_split_by_heuristics_chinese_chapter_markers() -> None:
    body = "内容内容内容。" * 60
    doc = "第一章 引言\n" + body + "\n\n第二章 方法\n" + body
    cfg = SplitterConfig(
        chunk_size=200, chunk_overlap=20, separators=["。"], languages=[LANG_CHINESE]
    )
    chunks = split_by_heuristics_impl(doc, cfg, None)
    assert len(chunks) >= 2


def test_split_by_heuristics_falls_through_for_unstructured_doc() -> None:
    doc = "plain prose without structure. " * 5
    cfg = SplitterConfig(chunk_size=1000, chunk_overlap=20)
    chunks = split_by_heuristics_impl(doc, cfg, None)
    assert len(chunks) == 1


def test_split_by_heuristics_oversize_block_recurses_into_legacy() -> None:
    huge = "This is a long sentence. " * 200  # ~5000 字符
    doc = "1. Intro\n" + huge
    cfg = SplitterConfig(chunk_size=500, chunk_overlap=50, separators=[". "])
    chunks = split_by_heuristics_impl(doc, cfg, None)
    assert len(chunks) >= 5
    for i, c in enumerate(chunks):
        assert len(c.content) <= 2 * cfg.chunk_size, f"chunk {i} exceeds 2x size"


def test_split_by_heuristics_boundaries_are_ordered() -> None:
    doc = "Kapitel 1: A\nbody\n\n---\n\n2. Section B\nbody\n\nPage 3 of 10\n\n第三章 C\nbody"
    bounds = find_heuristic_boundaries(doc, None)
    assert len(bounds) >= 2
    for i in range(1, len(bounds)):
        assert bounds[i].rune_start >= bounds[i - 1].rune_start


def test_split_by_heuristics_empty_text() -> None:
    assert split_by_heuristics_impl("", default_config(), None) == []


def test_split_by_heuristics_overlap_actually_overlaps() -> None:
    """回归：applyOverlapAligned 以前把 curEnd 自己也纳入边界搜索，而 curEnd
    必然是一个边界（装箱器总在边界处 flush），于是永远返回 curEnd，
    无论 ChunkOverlap 配成多少都是零重叠。"""
    parts: list[str] = []
    for i in range(1, 13):
        parts.append("\n\n")
        parts.append(str(i % 10))
        parts.append(". ")
        parts.append("alpha beta gamma. " * 4)  # 每节约 72 字符
    doc = "".join(parts)

    cfg = SplitterConfig(chunk_size=200, chunk_overlap=80, separators=[". "])
    chunks = split_by_heuristics_impl(doc, cfg, None)
    assert len(chunks) >= 2

    saw = False
    for i in range(1, len(chunks)):
        prev = chunks[i - 1].content.strip()
        cur = chunks[i].content.strip()
        match = 0
        max_scan = min(len(prev), len(cur))
        for n in range(1, max_scan + 1):
            if cur.startswith(prev[len(prev) - n :]):
                match = n
        if match >= 20:
            saw = True
            break
    assert saw, f"expected a >=20 char overlap, none did. chunk sizes: {chunk_lengths(chunks)}"


def test_split_by_heuristics_drops_boundaries_inside_protected_spans() -> None:
    body = "filler. " * 30
    # LaTeX 块中间那行命中 NumberedSectionPattern，过滤器应丢掉该边界，
    # 让公式块保持完整。
    doc = body + "\n\n$$\nx = 1\n1. equation step one\ny = 2\n$$\n\n" + body

    bounds = find_heuristic_boundaries(doc, None)
    prot = protected_spans_rune(doc, protected_spans(doc))
    assert prot, "expected protected spans for doc, got none"
    filtered = drop_bounds_inside_spans(bounds, prot)
    for b in filtered:
        for s in prot:
            assert not (b.rune_start > s.start and b.rune_start < s.end)
    assert len(filtered) < len(bounds)
