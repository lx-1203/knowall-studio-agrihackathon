"""profiler_test.go 的逐条翻译。"""

from __future__ import annotations

import pytest

from app.core.parsing.chunking import (
    TIER_HEADING,
    TIER_HEURISTIC,
    TIER_LEGACY,
    profile_document,
    select_strategy,
)


def test_profile_document_empty() -> None:
    p = profile_document("")
    assert p.total_chars == 0
    assert p.total_lines == 0


def test_profile_document_markdown_headings() -> None:
    doc = """# Title
Some intro text here.

## Section 1
Body of section 1.

## Section 2
Body of section 2.

### Subsection 2.1
Detail.

## Section 3
More body."""
    p = profile_document(doc)
    assert p.md_heading_counts[1] == 1
    assert p.md_heading_counts[2] == 3
    assert p.md_heading_counts[3] == 1
    assert p.md_heading_total == 5
    assert p.dominant_heading_level() == 2


def test_profile_document_dominant_level_fallback() -> None:
    # 没有层级达到 3 次 —— 回落到出现过的层级
    doc = "# Single H1\n## H2 a\n## H2 b\n"
    p = profile_document(doc)
    assert p.dominant_heading_level() == 2


def test_profile_document_numbered_sections() -> None:
    doc = """1. Introduction
text

2. Methodology
text

3. Results
text"""
    p = profile_document(doc)
    assert p.numbered_section_count >= 3


def test_profile_document_german_chapters() -> None:
    doc = "Kapitel 1: Einführung\n\nText\n\nKapitel 2: Hauptteil\n\nText"
    p = profile_document(doc)
    assert p.german_chapter_count == 2


def test_profile_document_chinese_chapters() -> None:
    doc = "第一章 引言\n\n内容\n\n第二章 方法\n\n内容"
    p = profile_document(doc)
    assert p.chinese_chapter_count == 2


def test_profile_document_form_feed() -> None:
    doc = "page 1 content\f\npage 2 content\f\npage 3 content"
    p = profile_document(doc)
    assert p.form_feed_count == 2


def test_profile_document_detects_code_block() -> None:
    doc = "Some prose.\n\n```go\nfunc main() {}\n```\n\nMore prose."
    p = profile_document(doc)
    assert p.has_code is True


def test_profile_document_detects_table() -> None:
    doc = "Intro.\n\n| col a | col b |\n| --- | --- |\n| 1 | 2 |\n"
    p = profile_document(doc)
    assert p.has_tables is True


def test_profile_document_line_statistics() -> None:
    doc = "short\nthis is a longer line of text\nanother line here"
    p = profile_document(doc)
    assert p.total_lines == 3
    assert p.avg_line_len > 0


def test_select_strategy_heading_doc() -> None:
    doc = "# A\nbody\n## B\nbody\n## C\nbody\n## D\nbody"
    p = profile_document(doc)
    chain = select_strategy(p)
    assert chain[0] == TIER_HEADING


def test_select_strategy_heuristic_doc() -> None:
    doc = "Kapitel 1: Foo\nbody body body\n\n" + "Kapitel 2: Bar\nbody body body\n\n"
    p = profile_document(doc)
    chain = select_strategy(p)
    # 没有 Markdown 标题 → heuristic 必须排第一（heading 档被跳过）
    assert chain[0] == TIER_HEURISTIC


def test_select_strategy_plain_doc() -> None:
    doc = "just a paragraph of plain text without any structure indicators at all here"
    p = profile_document(doc)
    chain = select_strategy(p)
    assert chain[0] == TIER_LEGACY


@pytest.mark.parametrize("doc", ["", "simple", "# H1\nbody"])
def test_select_strategy_always_falls_back_to_legacy(doc: str) -> None:
    p = profile_document(doc)
    chain = select_strategy(p)
    assert chain[-1] == TIER_LEGACY
