"""Markdown 表格规整的行为规格。

逐条翻译自 docreader/tests/test_markdown_table_util.py。上游那个用例依赖
markitdown 转换 fixtures/en_tables.docx，本仓库没有这份 fixture，跳过。
"""
import pytest

from app.core.parsing.docreader.markdown_parser import MarkdownTableUtil


def test_preserves_empty_cells():
    """中间的空单元格不能被格式化吃掉。"""
    raw = "| a |  | c |\n| --- | --- | --- |\n| 1 | 2 | 3 |"
    formatted = MarkdownTableUtil().format_table(raw)
    assert "| a |  | c |" in formatted
    assert formatted.count("|") == raw.count("|")


def test_format_nonempty_table():
    raw = "|Name|Age|\n|---|---|\n|John|30|"
    formatted = MarkdownTableUtil().format_table(raw)
    assert "| Name | Age |" in formatted
    assert "| --- | --- |" in formatted
    assert "| John | 30 |" in formatted


@pytest.mark.skip(reason="上游 fixtures/en_tables.docx 未随仓库提供")
def test_normalize_markitdown_en_tables():
    pass


def test_malformed_unclosed_row_is_passthrough():
    """不闭合的行必须以线性时间原样放过。

    回归 Tencent/WeKnora#2768：旧的整行正则在这类行上会指数级回溯，卡死
    docreader 的单线程事件循环。
    """
    line = "| " + " | ".join(f"col{i}" for i in range(100)) + "  unclosed"
    formatted = MarkdownTableUtil().format_table(line)
    assert formatted == line


def test_long_valid_table_formats():
    """再长但合法的表格也要线性时间处理完。"""
    header = "| " + " | ".join(f"c{i}" for i in range(200)) + " |"
    sep = "| " + " | ".join("---" for _ in range(200)) + " |"
    row = "| " + " | ".join(f"v{i}" for i in range(200)) + " |"
    formatted = MarkdownTableUtil().format_table("\n".join([header, sep, row]))
    assert "| c0 | c1 |" in formatted
    assert "| c199 |" in formatted
    assert "| --- |" in formatted
    assert "| v0 | v1 |" in formatted
    assert "| v199 |" in formatted


def test_lone_pipe_is_passthrough():
    """单独一个竖线不是表格行，不能被删掉。"""
    assert MarkdownTableUtil().format_table("|") == "|"
    assert MarkdownTableUtil().format_table("  |  ") == "  |  "


def test_lone_pipe_above_table_is_kept():
    raw = "|\n| a | b |\n| --- | --- |\n| 1 | 2 |"
    formatted = MarkdownTableUtil().format_table(raw)
    assert formatted.split("\n")[0] == "|"
    assert "| a | b |" in formatted
    assert "| 1 | 2 |" in formatted


def test_alignment_colons_preserved():
    raw = "| a | b | c |\n| :---------- | -------: | :------: |\n| 1 | 2 | 3 |"
    formatted = MarkdownTableUtil().format_table(raw)
    assert "| :--- | ---: | :---: |" in formatted


def test_crlf_table_formats_without_mixed_endings():
    raw = "# Title\r\n\r\n|Name|Age|\r\n|---|---|\r\n|John|30|\r\n\r\nparagraph\r\n"
    formatted = MarkdownTableUtil().format_table(raw)
    assert "| Name | Age |" in formatted
    assert "| --- | --- |" in formatted
    assert "| John | 30 |" in formatted
    assert "paragraph" in formatted
    assert "\r" not in formatted
    assert formatted.endswith("\n")
