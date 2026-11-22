"""patterns_test.go 的逐条翻译（表驱动用例展开为 parametrize）。"""

from __future__ import annotations

import pytest

from app.core.parsing.chunking import (
    AllCapsHeadingPattern,
    ChineseChapterPattern,
    EnglishChapterPattern,
    GermanChapterPattern,
    LANG_CHINESE,
    LANG_ENGLISH,
    LANG_GERMAN,
    MarkdownHeadingPattern,
    NumberedSectionPattern,
    PageFooterPattern,
    VisualSeparatorPattern,
    chapter_patterns_for_langs,
    sentence_separators,
)


@pytest.mark.parametrize(
    "line,expected",
    [
        ("# Heading 1", True),
        ("## Heading 2", True),
        ("###### Heading 6", True),
        ("####### Too many", False),
        ("#NoSpace", False),
        ("  # Indented", False),
        ("plain text", False),
    ],
)
def test_markdown_heading_pattern_basic_levels(line: str, expected: bool) -> None:
    assert bool(MarkdownHeadingPattern.search(line)) is expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ("1. Introduction", True),
        # 多级编号不再要求收尾的点
        ("2.3 Methodology", True),
        ("2.3. Methodology", True),
        # 三级编号 + 中文标题 + 无收尾点
        ("2.2.1 用户与权限", True),
        ("3.2.1 单机 Docker Compose", True),
        ("IV. Results", True),
        # 编号与标题之间必须有空白
        ("1.Introduction", False),
        # 必须有标题
        ("1.", False),
        ("1.1", False),
        # 单级编号仍然需要收尾的点
        ("1 NoDotSingleLevel", False),
        # 超过 3 层子编号不接受
        ("1.2.3.4.5 TooDeep", False),
        ("plain text", False),
    ],
)
def test_numbered_section_pattern(line: str, expected: bool) -> None:
    assert bool(NumberedSectionPattern.search(line)) is expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ("Kapitel 1: Einführung", True),
        ("Abschnitt 2.3 Methodik", True),
        ("Abschnitt 3 Methodik", True),
        ("Teil II Ergebnisse", True),
        ("chapter 1", False),
    ],
)
def test_german_chapter_pattern(line: str, expected: bool) -> None:
    assert bool(GermanChapterPattern.search(line)) is expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ("Chapter 1: Intro", True),
        ("Section 5 Methods", True),
        ("Part IV Results", True),
        ("Kapitel 1", False),
    ],
)
def test_english_chapter_pattern(line: str, expected: bool) -> None:
    assert bool(EnglishChapterPattern.search(line)) is expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ("第一章 引言", True),
        ("第3节 方法论", True),
        ("第二部分 结果", True),
        # 第 / 数字 / 单位之间有空格
        ("第 1 章 引言", True),
        ("第 一 章 引言", True),
        ("第1章 引言", True),
        ("Chapter 1", False),
        # 缺数字
        ("第 章 空数字", False),
    ],
)
def test_chinese_chapter_pattern(line: str, expected: bool) -> None:
    assert bool(ChineseChapterPattern.search(line)) is expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ("---", True),
        ("========", True),
        ("***", True),
        ("____", True),
        # 至少 3 个字符
        ("--", False),
        ("-- text", False),
    ],
)
def test_visual_separator_pattern(line: str, expected: bool) -> None:
    assert bool(VisualSeparatorPattern.search(line)) is expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ("Seite 3 von 24", True),
        ("Page 5 of 12", True),
        ("page 7", True),
        ("Seite 9", True),
        ("页 3", True),
        ("页码 3 / 12", True),
        ("Some text", False),
    ],
)
def test_page_footer_pattern(line: str, expected: bool) -> None:
    assert bool(PageFooterPattern.search(line)) is expected


@pytest.mark.parametrize(
    "line,expected",
    [
        ("INTRODUCTION", True),
        ("METHODS AND MATERIALS", True),
        ("Mixed Case Heading", False),
        # 少于 4 个字符
        ("ABC", False),
    ],
)
def test_all_caps_heading_pattern(line: str, expected: bool) -> None:
    assert bool(AllCapsHeadingPattern.search(line)) is expected


def test_sentence_separators() -> None:
    assert sentence_separators(LANG_CHINESE)[0] == "。"
    assert sentence_separators(LANG_ENGLISH)[0] == ". "
    assert len(sentence_separators("xx")) >= 5


def test_chapter_patterns_for_langs() -> None:
    assert len(chapter_patterns_for_langs(None)) == 3
    assert len(chapter_patterns_for_langs([LANG_GERMAN])) == 1
    assert len(chapter_patterns_for_langs([LANG_GERMAN, LANG_CHINESE])) == 2
    assert len(chapter_patterns_for_langs(["xx"])) == 3
