"""heading_splitter_test.go 的逐条翻译。"""

from __future__ import annotations

import pytest

from app.core.parsing.chunking import (
    SplitterConfig,
    common_heading_prefix,
    split_by_headings_impl,
)


def test_split_by_headings_basic_sections() -> None:
    # 每个小节都刻意大于合并目标（约 ChunkSize/2），这样切分后的 coalesce
    # 会保留它们为独立 chunk。这里测的是逐小节产出 + 面包屑，不是合并。
    body = "Lorem ipsum dolor sit amet consectetur adipiscing elit. " * 4
    doc = (
        "# Top\n" + body
        + "\n\n## Section A\n" + body
        + "\n\n## Section B\n" + body
        + "\n\n## Section C\n" + body
    )
    cfg = SplitterConfig(chunk_size=300, chunk_overlap=0)
    chunks = split_by_headings_impl(doc, cfg, None)
    assert len(chunks) >= 3

    # 面包屑走 ContextHeader，不在 Content 里
    for i, c in enumerate(chunks):
        assert "# Top" in c.context_header, f"chunk {i} missing H1 in ContextHeader"
        assert "# Top" in c.embedding_content(), f"chunk {i} EmbeddingContent missing H1"

    assert any("## Section B" in c.content and "Lorem ipsum" in c.content for c in chunks)


def test_split_by_headings_falls_through_for_unstructured_doc() -> None:
    doc = "Just a plain paragraph without any headings at all in this text."
    cfg = SplitterConfig(chunk_size=200, chunk_overlap=0)
    chunks = split_by_headings_impl(doc, cfg, None)
    # 没有标题 → 回落到 SplitText，整段保持为一个 chunk
    assert len(chunks) == 1


def test_split_by_headings_large_section_recurses_into_legacy() -> None:
    body = "This is a long sentence repeated many times. " * 50
    doc = "# Top\n## Big\n" + body
    cfg = SplitterConfig(chunk_size=300, chunk_overlap=30, separators=[". "])
    chunks = split_by_headings_impl(doc, cfg, None)
    assert len(chunks) >= 2
    for i, c in enumerate(chunks):
        assert "# Top" in c.context_header, f"sub-chunk {i} missing H1 in ContextHeader"


def test_split_by_headings_breadcrumb_reflects_latest_path() -> None:
    body = "Lorem ipsum dolor sit amet consectetur adipiscing elit. " * 4
    doc = "# Chapter 1\n" + body + "\n\n## Section A\n" + body + "\n\n## Section B\n" + body
    cfg = SplitterConfig(chunk_size=300, chunk_overlap=0)
    chunks = split_by_headings_impl(doc, cfg, None)
    assert len(chunks) >= 3
    for c in chunks:
        if "text B" in c.content:
            assert "## Section A" not in c.context_header
            assert "## Section B" in c.context_header


def test_split_by_headings_ignores_headings_inside_code_fence() -> None:
    doc = "# Real\n\n```\n# Fake heading inside code\n```\n\nbody"
    cfg = SplitterConfig(chunk_size=500, chunk_overlap=0)
    chunks = split_by_headings_impl(doc, cfg, None)
    assert any("# Real" in c.context_header or "# Real" in c.content for c in chunks)


def test_split_by_headings_preserves_position_relative_to_original() -> None:
    doc = "# Top\nintro\n\n## A\nbody A\n\n## B\nbody B"
    cfg = SplitterConfig(chunk_size=500, chunk_overlap=0)
    chunks = split_by_headings_impl(doc, cfg, None)
    for i, c in enumerate(chunks):
        assert c.start >= 0, f"chunk {i} has negative Start"
        assert c.end >= c.start, f"chunk {i} End < Start"


def test_split_by_headings_position_invariant() -> None:
    """End-Start == len(Content)，且 runes[Start:End] == Content。"""
    doc = """# Top
intro paragraph here.

## Section A
content of A here, several sentences.

## Section B
content of B here.

## Section C
content of C here."""
    cfg = SplitterConfig(chunk_size=200, chunk_overlap=20)
    chunks = split_by_headings_impl(doc, cfg, None)
    assert chunks
    doc_runes = list(doc)
    for i, c in enumerate(chunks):
        assert c.end - c.start == len(c.content), f"chunk {i}: span != content_runes"
        if c.start >= 0 and c.end <= len(doc_runes):
            assert "".join(doc_runes[c.start : c.end]) == c.content


def test_split_by_headings_coalesces_tiny_adjacent_sections() -> None:
    doc = """# Install Log

## Docker镜像
使用 daocloud 部署 v0.3.1。

## 前端老版本
浏览器缓存了旧前端资源。

## 登录报错
ERROR: column missing.

## 解析失败
embedding 表缺列。"""
    cfg = SplitterConfig(chunk_size=500, chunk_overlap=0)
    chunks = split_by_headings_impl(doc, cfg, None)
    assert chunks
    assert len(chunks) < 5
    for i, c in enumerate(chunks):
        assert "# Install Log" in c.context_header, f"chunk {i} missing parent H1"
    for h in ["## Docker镜像", "## 前端老版本", "## 登录报错", "## 解析失败"]:
        assert any(h in c.content for c in chunks), f"merged chunks should still contain {h!r}"


def test_split_by_headings_does_not_coalesce_distinct_top_level_headings() -> None:
    doc = """# Intro
short intro.

# Usage
short usage.

# FAQ
short faq."""
    cfg = SplitterConfig(chunk_size=500, chunk_overlap=0)
    chunks = split_by_headings_impl(doc, cfg, None)
    assert len(chunks) == 3
    for i, heading in enumerate(["# Intro", "# Usage", "# FAQ"]):
        assert heading in chunks[i].content


def test_split_by_headings_coalesce_preserves_position_invariant() -> None:
    doc = """# Top

## A
short A.

## B
short B.

## C
short C."""
    cfg = SplitterConfig(chunk_size=500, chunk_overlap=0)
    chunks = split_by_headings_impl(doc, cfg, None)
    doc_runes = list(doc)
    for i, c in enumerate(chunks):
        assert c.end - c.start == len(c.content), f"chunk {i}: span != content_runes after merge"
        if c.start >= 0 and c.end <= len(doc_runes):
            assert "".join(doc_runes[c.start : c.end]) == c.content


def test_split_by_headings_coalesce_respects_chunk_size() -> None:
    sections = 30
    parts = ["# Doc\n"]
    for _ in range(sections):
        parts.append("\n## Section ")
        parts.append("X")
        parts.append("\nshort body line.\n")
    cfg = SplitterConfig(chunk_size=200, chunk_overlap=0)
    chunks = split_by_headings_impl("".join(parts), cfg, None)
    for i, c in enumerate(chunks):
        assert len(c.content) <= cfg.chunk_size, f"chunk {i} exceeds ChunkSize"


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("# Top\n## A", "# Top\n## B", "# Top"),
        ("# Top", "# Top", "# Top"),
        ("# X", "# Y", ""),
        ("# Top\n## A\n### x", "# Top\n## A\n### y", "# Top\n## A"),
        ("", "# Top", ""),
    ],
)
def test_common_heading_prefix(a: str, b: str, expected: str) -> None:
    assert common_heading_prefix(a, b) == expected


def test_split_by_headings_no_breadcrumb_duplication() -> None:
    doc = """# Chapter 1
intro.

## Section A
body A.

## Section B
body B."""
    cfg = SplitterConfig(chunk_size=500, chunk_overlap=0)
    chunks = split_by_headings_impl(doc, cfg, None)
    for i, c in enumerate(chunks):
        for heading in ["## Section A", "## Section B"]:
            assert c.content.count(heading) <= 1, f"chunk {i} contains {heading!r} twice"


def test_split_by_headings_deep_sub_heading_in_large_section() -> None:
    filler = "clause body sentence that pads the section out. " * 20
    doc = (
        "# Standard XYZ\n"
        + "## Preface\n" + filler + "\n\n"
        + "## 5 Classification\n" + filler + "\n\n"
        + "### 5.9 Grade Nine\n" + filler + "\n\n"
        + "#### 5.9.2 Clause Series\n" + filler + "\n\n"
        + "the item users search for is MARKER_ITEM_23 graded here.\n\n"
        + "## Appendix A\n" + filler + "\n\n"
        + "## Appendix B\n" + filler + "\n\n"
        + "## Appendix C\n" + filler
    )
    cfg = SplitterConfig(chunk_size=300, chunk_overlap=0, separators=[". "])
    chunks = split_by_headings_impl(doc, cfg, None)

    marker = next((c for c in chunks if "MARKER_ITEM_23" in c.content), None)
    assert marker is not None, "no chunk contains the marker line"
    assert "#### 5.9.2 Clause Series" in marker.context_header
    assert "## 5 Classification" in marker.context_header
