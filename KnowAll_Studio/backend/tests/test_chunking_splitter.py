"""splitter_test.go 的逐条翻译（ExtractImageRefs 一条除外，见文件末尾）。"""

from __future__ import annotations

import pytest

from app.core.parsing.chunking import (
    Chunk,
    HeaderTracker,
    SplitUnit,
    SplitterConfig,
    build_units_with_protection,
    compute_overlap,
    default_config,
    find_semantic_overlap_boundary,
    find_semantic_overlap_boundary_ending_at_or_after,
    new_header_tracker,
    protected_spans,
    split_by_separators,
    split_text,
    split_text_parent_child,
    units_text,
)


# ---------------------------------------------------------------------------
# Start/End 还原工具 —— 只靠 chunk 的 Start/End 还原原文。
# 带前置表头的 chunk，其表头是"虚拟"前缀，长度 = runeLen(Content) - (End - Start)，
# 原文部分就是 Content 的最后 (End-Start) 个 rune。
# ---------------------------------------------------------------------------


def restore_text_from_chunks(chunks: list[Chunk]) -> str:
    if not chunks:
        return ""

    # 按 End 升序、Start 升序排序（与 Python 参考实现 restore_text 的顺序一致）
    ordered = sorted(chunks, key=lambda c: (c.end, c.start))

    result: list[str] = []
    last_end = 0

    for c in ordered:
        if c.end <= last_end:
            continue  # 已被之前的 chunk 完全覆盖

        content_runes = list(c.content)
        span_len = c.end - c.start
        header_len = len(content_runes) - span_len
        if header_len < 0:
            header_len = 0
        # originalPortion 是 text[Start:End] 部分，不含前置表头
        original_portion = content_runes[header_len:]

        # 只取 lastEnd 之后的部分（跳过重叠）
        new_start = 0
        if last_end > c.start:
            new_start = last_end - c.start
        if new_start < len(original_portion):
            result.extend(original_portion[new_start:])

        last_end = c.end

    return "".join(result)


def max_chunk_runes(chunks: list[Chunk]) -> int:
    return max((len(c.content) for c in chunks), default=0)


def test_split_text_basic_ascii() -> None:
    text = "Hello world. This is a test."
    cfg = SplitterConfig(chunk_size=100, chunk_overlap=0, separators=[". "])
    chunks = split_text(text, cfg)
    assert len(chunks) > 0
    assert "".join(c.content for c in chunks) == text


def test_split_text_chinese_text_start_end_are_rune_offsets() -> None:
    # 每个汉字 3 字节但只有 1 个 rune。本用例确保 Start/End 是 rune 偏移。
    text = "你好世界这是一个测试文本用于检验分割位置"
    rune_count = len(text)
    byte_count = len(text.encode("utf-8"))
    assert rune_count != byte_count

    cfg = SplitterConfig(chunk_size=100, chunk_overlap=0, separators=["\n"])
    chunks = split_text(text, cfg)

    assert len(chunks) == 1
    c = chunks[0]
    assert c.start == 0
    assert c.end == rune_count


def test_split_text_chinese_multi_chunk_start_end_consistency() -> None:
    line = "这是一段中文内容用于测试分割功能是否正确。"
    text = (line + "\n\n") * 20
    text = text.rstrip("\n")

    cfg = SplitterConfig(chunk_size=30, chunk_overlap=5, separators=["\n\n", "\n", "。"])
    chunks = split_text(text, cfg)

    assert len(chunks) >= 2

    text_runes = list(text)
    for i, c in enumerate(chunks):
        content_rune_len = len(c.content)
        assert c.end - c.start == content_rune_len, f"chunk[{i}]: span != content rune len"
        assert c.start >= 0
        assert c.end <= len(text_runes)
        assert "".join(text_runes[c.start : c.end]) == c.content


def test_split_text_mixed_chinese_and_ascii() -> None:
    text = "Hello你好World世界Test测试"
    cfg = SplitterConfig(chunk_size=100, chunk_overlap=0, separators=["\n"])
    chunks = split_text(text, cfg)

    assert len(chunks) == 1
    c = chunks[0]
    assert c.end - c.start == len(text)


def test_split_text_protected_pattern_chinese_context() -> None:
    text = "这是前面的中文内容。![图片描述](http://example.com/img.png)这是后面的中文内容。"
    cfg = SplitterConfig(chunk_size=200, chunk_overlap=0, separators=["。"])
    chunks = split_text(text, cfg)

    text_runes = list(text)
    for i, c in enumerate(chunks):
        assert c.start >= 0 and c.end <= len(text_runes)
        assert "".join(text_runes[c.start : c.end]) == c.content


def test_split_text_simulate_merge_slicing() -> None:
    # 模拟 merge.go:104-106 的切片，确保不会 panic —— 那正是线上崩溃的写法。
    line = "这是第一段内容用于模拟知识库问答的文本"
    text = line + "\n\n" + line + "\n\n" + line

    cfg = SplitterConfig(chunk_size=25, chunk_overlap=5, separators=["\n\n", "\n"])
    chunks = split_text(text, cfg)
    assert len(chunks) >= 2

    for i in range(1, len(chunks)):
        prev = chunks[i - 1]
        curr = chunks[i]
        if curr.start > prev.end:
            continue
        content_runes = list(curr.content)
        offset = len(content_runes) - (curr.end - prev.end)
        assert offset >= 0
        assert offset <= len(content_runes)
        _ = "".join(content_runes[offset:])


def test_split_text_recursive_separators_no_oversize_chunks() -> None:
    """回归：选定第一个能切出多片的分隔符后，仍然过大的子片以前不会再用
    下一个分隔符继续切。真实文档常见"一个段落分隔 + 一长串换行分隔的行"，
    这类输入必须被继续切分。"""
    body = "This is one fairly short line of text.\n" * 50
    text = "lead paragraph that is short.\n\n" + body
    cfg = SplitterConfig(chunk_size=300, chunk_overlap=30, separators=["\n\n", "\n", ". "])
    chunks = split_text(text, cfg)
    assert len(chunks) >= 2
    max_allowed = cfg.chunk_size * 3 // 2
    for i, c in enumerate(chunks):
        assert len(c.content) <= max_allowed, f"chunk {i} exceeds 1.5x ChunkSize"


def test_split_text_empty() -> None:
    assert len(split_text("", default_config())) == 0


def test_split_text_single_char_chinese() -> None:
    text = "你"
    cfg = SplitterConfig(chunk_size=10, chunk_overlap=0, separators=["\n"])
    chunks = split_text(text, cfg)
    assert len(chunks) == 1
    assert (chunks[0].start, chunks[0].end) == (0, 1)


def test_split_text_latex_block_in_chinese() -> None:
    text = "前面的文字$$E=mc^2$$后面的文字"
    cfg = SplitterConfig(chunk_size=200, chunk_overlap=0, separators=["\n"])
    chunks = split_text(text, cfg)

    text_runes = list(text)
    for i, c in enumerate(chunks):
        assert c.end - c.start == len(c.content)
        assert c.end <= len(text_runes)


def test_split_text_code_block_in_chinese() -> None:
    text = "中文描述\n```python\nprint('hello')\n```\n继续中文"
    cfg = SplitterConfig(chunk_size=200, chunk_overlap=0, separators=["\n\n", "\n"])
    chunks = split_text(text, cfg)

    text_runes = list(text)
    for i, c in enumerate(chunks):
        assert c.start >= 0 and c.end <= len(text_runes)
        assert "".join(text_runes[c.start : c.end]) == c.content


def test_split_text_overlap_chunks_non_negative_start() -> None:
    text = "中文测试内容，" * 50
    cfg = SplitterConfig(chunk_size=20, chunk_overlap=5, separators=["，"])
    chunks = split_text(text, cfg)

    for i, c in enumerate(chunks):
        assert c.start >= 0
        assert c.end >= c.start


@pytest.mark.parametrize(
    "text,expected",
    [
        ("第一句。第二句\n普通换行后的内容\n\n最后一段", "最后一段"),
        ("第一句。第二句。第三句", "第二句。第三句"),
        ("第一段。\r\n\r\n第二段", "第二段"),
        ("第一句。第一行\n第二行", "第二行"),
        ("第一句。第一行\r\n第二行", "第二行"),
        ("第一句？第二句", "第二句"),
        ("第一句！第二句", "第二句"),
        ("First sentence. Second sentence", "Second sentence"),
        ("Question? Answer", "Answer"),
        ("Warning! Continue", "Continue"),
    ],
)
def test_find_semantic_overlap_boundary_priority_then_earliest(text: str, expected: str) -> None:
    end, ok = find_semantic_overlap_boundary(text)
    assert ok
    assert "".join(list(text)[end:]) == expected


@pytest.mark.parametrize(
    "text",
    [
        "没有任何语义分隔符的连续文本",
        "只有，逗号；分号：冒号",
        "version1.2 remains one unit",
        "address 192.168.1.1 remains one unit",
        "see https://ex.com?q=1&foo=bar",
    ],
)
def test_find_semantic_overlap_boundary_no_configured_semantic_separator(text: str) -> None:
    end, ok = find_semantic_overlap_boundary(text)
    assert not ok, f"returned boundary at {end}"


@pytest.mark.parametrize(
    "text",
    [
        '代码是 `fmt.Println("hello. world")` 后续内容',
        '代码块 ```go\nfmt.Println("hello. world")\n``` 后续内容',
        "公式 $$x. y$$ 后续内容",
    ],
)
def test_find_semantic_overlap_boundary_ignores_protected_content(text: str) -> None:
    end, ok = find_semantic_overlap_boundary(text)
    assert not ok, f"returned protected boundary at {end}"


@pytest.mark.parametrize(
    "text,min_end,expected",
    [
        ("a。bc。XYZ", 4, "XYZ"),
        ("\n\nx? tail", 3, "tail"),
    ],
)
def test_find_semantic_overlap_boundary_filters_eligibility_before_priority_and_position(
    text: str, min_end: int, expected: str
) -> None:
    end, ok = find_semantic_overlap_boundary_ending_at_or_after(text, min_end)
    assert ok
    assert "".join(list(text)[end:]) == expected


def test_compute_overlap_finds_boundary_inside_large_unit() -> None:
    text = "abcdefgh。尾巴内容"
    unit = SplitUnit(text=text, start=100, end=100 + len(text))

    overlap, overlap_len = compute_overlap([unit], 8, 32, 8)
    assert units_text(overlap) == "尾巴内容"
    assert overlap_len == len("尾巴内容")
    assert len(overlap) == 1 and (overlap[0].start, overlap[0].end) == (109, 113)


def test_compute_overlap_no_boundary_means_no_overlap() -> None:
    text = "abcdefgh尾巴内容"
    unit = SplitUnit(text=text, start=0, end=len(text))

    overlap, overlap_len = compute_overlap([unit], 8, 32, 8)
    assert len(overlap) == 0 and overlap_len == 0


def test_compute_overlap_respects_next_chunk_capacity() -> None:
    text = "abcdefgh。尾巴"
    unit = SplitUnit(text=text, start=0, end=len(text))

    overlap, overlap_len = compute_overlap([unit], 10, 8, 5)
    assert units_text(overlap) == "尾巴"
    assert overlap_len + 5 <= 8


@pytest.mark.parametrize(
    "text,expected",
    [
        ("abcd。WXYZ", "WXYZ"),
        ("\r\n\r\nWXYZ", "WXYZ"),
        ("a。bcWXYZ", ""),
        ("abc. WXY", "WXY"),
    ],
)
def test_compute_overlap_lookbehind_boundary_eligibility(text: str, expected: str) -> None:
    unit = SplitUnit(text=text, start=0, end=len(text))
    overlap, overlap_len = compute_overlap([unit], 4, 20, 4)
    assert units_text(overlap) == expected
    assert overlap_len == len(expected)
    assert overlap_len <= 4


def test_split_text_overlap_does_not_break_url_query() -> None:
    line1 = "a" * 35
    text = line1 + "\n" + "https://ex.com?q=1 tail\n" + "more content here"
    cfg = SplitterConfig(chunk_size=40, chunk_overlap=10, separators=["\n\n", "\n"])

    chunks = split_text(text, cfg)
    assert len(chunks) >= 3
    assert not chunks[2].content.startswith("q=1")


def test_build_units_with_protection_inline_code_not_split() -> None:
    text = "intro `code.here` suffix"
    units = build_units_with_protection(text, protected_spans(text), ["\n"], 0)

    for u in units:
        if "code.here" in u.text:
            assert "`code.here`" in u.text


def test_split_text_semantic_overlap_inside_paragraph() -> None:
    first = "甲" * 15 + "。尾巴"
    text = first + "\n\n" + "后续内容"
    cfg = SplitterConfig(chunk_size=20, chunk_overlap=10, separators=["\n\n", "\n", "。"])

    chunks = split_text(text, cfg)
    assert len(chunks) == 2
    assert chunks[1].content.startswith("尾巴\n\n")
    assert chunks[1].end - chunks[1].start == len(chunks[1].content)


def test_build_units_with_protection_rune_offsets() -> None:
    text = "你好世界"
    units = build_units_with_protection(text, [], ["\n"], 0)

    assert len(units) == 1
    u = units[0]
    expected_rune_len = 4  # 4 个汉字
    assert u.start == 0
    assert u.end == expected_rune_len  # 不是字节长度 12


def test_build_units_with_protection_with_protected_span() -> None:
    text = "前面![alt](url)后面"
    protected = protected_spans(text)
    units = build_units_with_protection(text, protected, ["\n"], 0)

    text_runes = list(text)
    for i, u in enumerate(units):
        assert u.end - u.start == len(u.text), f"unit[{i}] span != rune len"
        assert u.start >= 0 and u.end <= len(text_runes)


@pytest.mark.parametrize(
    "text,separators,expected_parts",
    [
        ("a\n\nb\n\nc", ["\n\n"], 5),
        ("abc", ["\n"], 1),
        ("a\nb\nc", ["\n"], 5),
        ("", ["\n"], 1),
    ],
)
def test_split_by_separators(text: str, separators: list[str], expected_parts: int) -> None:
    parts = split_by_separators(text, separators, 0)
    assert len(parts) == expected_parts


def test_split_text_large_chinese_document() -> None:
    parts = []
    for i in range(100):
        parts.append(f"第{i}段：这是一段用于测试的中文内容，包含各种常见的汉字和标点符号。")
        parts.append("\n\n")
    text = "".join(parts)

    cfg = SplitterConfig(chunk_size=50, chunk_overlap=10, separators=["\n\n", "\n", "。"])
    chunks = split_text(text, cfg)

    text_runes = list(text)
    for i, c in enumerate(chunks):
        assert c.end - c.start == len(c.content)
        assert c.start >= 0
        assert c.end <= len(text_runes)
        assert "".join(text_runes[c.start : c.end]) == c.content

    # 在所有重叠的相邻 chunk 对上模拟 merge.go 的逻辑
    for i in range(1, len(chunks)):
        prev = chunks[i - 1]
        curr = chunks[i]
        if curr.start > prev.end:
            continue
        content_runes = list(curr.content)
        offset = len(content_runes) - (curr.end - prev.end)
        assert 0 <= offset <= len(content_runes)


# ---------------------------------------------------------------------------
# 表头前置相关用例
# ---------------------------------------------------------------------------


def test_split_text_table_header_prepended_to_chunks() -> None:
    text = (
        "前面的文字\n\n"
        "| 姓名 | 年龄 | 城市 |\n"
        "| --- | --- | --- |\n"
        "| 张三 | 25 | 北京 |\n"
        "| 李四 | 30 | 上海 |\n"
        "| 王五 | 28 | 广州 |\n"
        "| 赵六 | 35 | 深圳 |\n"
        "| 孙七 | 22 | 杭州 |\n"
        "| 周八 | 40 | 成都 |\n"
        "\n后面的文字"
    )
    table_header = "| 姓名 | 年龄 | 城市 |\n| --- | --- | --- |\n"

    cfg = SplitterConfig(chunk_size=60, chunk_overlap=5, separators=["\n\n", "\n"])
    chunks = split_text(text, cfg)

    assert len(chunks) >= 3

    header_prepend_count = 0
    for c in chunks:
        has_later_row = any(
            k in c.content for k in ("| 李四", "| 王五", "| 赵六", "| 孙七", "| 周八")
        )
        if has_later_row and "| 张三" not in c.content:
            assert c.content.startswith(table_header), (
                f"chunk (seq={c.seq}) has table rows but is missing prepended header"
            )
            header_prepend_count += 1

    assert header_prepend_count > 0


def test_split_text_no_header_for_non_table_content() -> None:
    text = "这是一段普通的中文文本，不包含任何表格。\n\n" * 10
    cfg = SplitterConfig(chunk_size=30, chunk_overlap=5, separators=["\n\n", "\n"])
    chunks = split_text(text, cfg)

    text_runes = list(text)
    for i, c in enumerate(chunks):
        assert c.end - c.start == len(c.content)
        assert c.end <= len(text_runes)


def test_split_text_table_header_ended_by_empty_line() -> None:
    text = (
        "| A | B |\n"
        "| --- | --- |\n"
        "| 1 | 2 |\n"
        "| 3 | 4 |\n"
        "\n"
        "这是表格之后的普通文本内容，不应该包含表头。\n"
        "更多的普通文本内容用于填充。"
    )
    cfg = SplitterConfig(chunk_size=40, chunk_overlap=5, separators=["\n\n", "\n"])
    chunks = split_text(text, cfg)

    for c in chunks:
        has_table_row = "| A | B |" in c.content or "|" in c.content
        has_plain_text = "这是表格之后" in c.content or "更多的普通" in c.content
        if has_plain_text and not has_table_row:
            assert "| --- |" not in c.content


def test_header_tracker_basic_lifecycle() -> None:
    ht = new_header_tracker()

    ht.update("Some regular text")
    assert ht.get_headers() == ""

    ht.update("| A | B |\n| --- | --- |\n")
    assert ht.get_headers() != ""

    ht.update("| 1 | 2 |\n")
    assert ht.get_headers() != ""

    ht.update("\n")
    assert ht.get_headers() == ""

    ht.update("| X | Y |\n| --- | --- |\n")
    assert ht.get_headers() != ""


def test_header_tracker_empty_header_row_rewrite() -> None:
    # 有些转换器（如 MarkItDown）产出的表格表头行是空的：
    #   ||
    #   | --- | --- |
    #   | real col A | real col B |
    # 追踪器应把表头重写成规范的 Markdown 表头。
    ht = new_header_tracker()

    ht.update("||\n| --- | --- | --- |\n")
    h = ht.get_headers()
    assert h != ""

    ht.update("| 测试用例 ID | 测试模块 | 备注 |\n")
    h = ht.get_headers()

    assert "测试用例 ID" in h
    assert "||" not in h
    assert "---" in h
    assert h.index("测试用例 ID") < h.index("---")

    # 后续数据行不应被吸收进表头
    ht.update("| TC-001 | 模块A | 备注1 |\n")
    h2 = ht.get_headers()
    assert "TC-001" not in h2

    ht.update("\n")
    assert ht.get_headers() == ""


def test_header_tracker_normal_header_no_extension() -> None:
    ht = new_header_tracker()

    ht.update("| 姓名 | 年龄 |\n| --- | --- |\n")
    assert ht.get_headers() != ""

    ht.update("| 张三 | 25 |\n")
    assert "张三" not in ht.get_headers()


def test_split_text_empty_header_row_prepend() -> None:
    # 模拟 MarkItDown 输出：空表头行，真实列名在第一行数据行里。
    text = (
        "前言\n\n"
        "||\n"
        "| --- | --- | --- |\n"
        "| 用例ID | 模块 | 步骤 |\n"
        "| TC-001 | A | 步骤1 |\n"
        "| TC-002 | B | 步骤2 |\n"
        "| TC-003 | C | 步骤3 |\n"
        "| TC-004 | D | 步骤4 |\n"
        "\n"
        "结尾"
    )

    cfg = SplitterConfig(chunk_size=80, chunk_overlap=5, separators=["\n\n", "\n"])
    chunks = split_text(text, cfg)

    for c in chunks:
        has_later_row = any(k in c.content for k in ("TC-002", "TC-003", "TC-004"))
        if has_later_row and "TC-001" not in c.content:
            assert "用例ID" in c.content
            for line in c.content.split("\n"):
                trimmed = line.strip()
                is_only_pipes = trimmed != "" and all(r in "| " for r in trimmed)
                assert not is_only_pipes

        # 任何 chunk 内都不应出现重复行
        seen: dict[str, int] = {}
        for line in c.content.rstrip("\n").split("\n"):
            trimmed = line.strip()
            if trimmed == "" or "---" in trimmed:
                continue
            seen[trimmed] = seen.get(trimmed, 0) + 1
            assert seen[trimmed] <= 1, f"line appears twice in chunk (seq={c.seq}): {trimmed!r}"

    # 还原仍然可用
    assert restore_text_from_chunks(chunks) == text


def test_header_tracker_column_mismatch_ends_table() -> None:
    ht = new_header_tracker()
    ht.update("| Name | Game | Fame | Blame |\n| --- | --- | --- | --- |\n")
    assert ht.get_headers() != ""
    ht.update("| Sinple | Table |\n")
    assert ht.get_headers() == ""


def test_header_tracker_paragraph_break_ends_on_next_unit() -> None:
    ht = new_header_tracker()
    ht.update("| Name | Game | Fame | Blame |\n| --- | --- | --- | --- |\n")
    ht.update("| Russell Wilson | Football | High | Tacky uniform |\n\n")
    assert ht.get_headers() != ""
    assert ht.pending_table_break is True

    ht.update("| Sinple | Table |\n")
    assert ht.get_headers() == ""
    assert ht.header_ended_this_unit is True


def test_split_text_en_tables_no_cross_table_header() -> None:
    text = (
        "## A table, with and without a header row\n\n"
        "| Name | Game | Fame | Blame |\n"
        "| --- | --- | --- | --- |\n"
        "| Lebron James | Basketball | Very High | Leaving Cleveland |\n"
        "| Ryan Braun | Baseball | Moderate | Steroids |\n"
        "| Russell Wilson | Football | High | Tacky uniform |\n\n"
        "| Sinple | Table |\n"
        "| Without | Header |\n\n"
        "| Simple  Multiparagraph | Table  Full |\n"
        "| Of  Paragraphs | In each  Cell. |\n"
    )
    cfg = SplitterConfig(chunk_size=200, chunk_overlap=20, separators=["\n\n", "\n", "。"])
    chunks = split_text(text, cfg)
    assert len(chunks) >= 2

    for i, c in enumerate(chunks):
        has_sinple = "| Sinple | Table |" in c.content
        has_simple = "| Simple  Multiparagraph |" in c.content
        if has_sinple or has_simple:
            assert "| Name | Game | Fame | Blame |" not in c.content, f"chunk[{i}] carried table-1 header"


def test_split_text_multiple_tables_in_document() -> None:
    text = (
        "第一个表格：\n\n"
        "| 名称 | 值 |\n"
        "| --- | --- |\n"
        "| A | 1 |\n"
        "| B | 2 |\n"
        "| C | 3 |\n"
        "\n"
        "中间的文字\n\n"
        "| 项目 | 状态 |\n"
        "| --- | --- |\n"
        "| X | 完成 |\n"
        "| Y | 进行中 |\n"
        "| Z | 未开始 |\n"
        "\n"
        "结尾文字"
    )
    cfg = SplitterConfig(chunk_size=50, chunk_overlap=5, separators=["\n\n", "\n"])
    chunks = split_text(text, cfg)

    for c in chunks:
        if "| Y |" in c.content and "| X |" not in c.content:
            assert "| 项目 | 状态 |" in c.content
            assert "| 名称 | 值 |" not in c.content


def test_split_text_restore_text_no_table() -> None:
    parts = []
    for i in range(30):
        parts.append(f"第{i}段：这是一段用于测试的中文内容，包含各种标点符号。")
        parts.append("\n\n")
    text = "".join(parts)

    cfg = SplitterConfig(chunk_size=50, chunk_overlap=10, separators=["\n\n", "\n", "。"])
    chunks = split_text(text, cfg)

    assert restore_text_from_chunks(chunks) == text


def test_split_text_restore_text_with_table() -> None:
    text = (
        "这是文档前言部分的内容。\n\n"
        "| 姓名 | 城市 |\n"
        "| --- | --- |\n"
        "| 张三 | 北京 |\n"
        "| 李四 | 上海 |\n"
        "| 王五 | 广州 |\n"
        "| 赵六 | 深圳 |\n"
        "| 孙七 | 杭州 |\n"
        "| 周八 | 成都 |\n"
        "| 吴九 | 武汉 |\n"
        "| 郑十 | 南京 |\n"
        "\n"
        "这是表格之后的文字内容。\n"
        "这里还有更多的普通段落。"
    )

    cfg = SplitterConfig(chunk_size=80, chunk_overlap=5, separators=["\n\n", "\n"])
    chunks = split_text(text, cfg)
    text_runes = list(text)

    # 1. 基本位置不变式
    for i, c in enumerate(chunks):
        assert c.start >= 0
        assert c.end <= len(text_runes)
        assert c.end >= c.start

    # 2. text[Start:End] 与 Content 去掉前置表头后的部分一致
    for i, c in enumerate(chunks):
        content_runes = list(c.content)
        span_len = c.end - c.start
        header_len = len(content_runes) - span_len
        assert header_len >= 0, f"chunk[{i}]: Content rune len < span len"
        if c.start >= 0 and c.end <= len(text_runes):
            assert "".join(text_runes[c.start : c.end]) == "".join(content_runes[header_len:])

    # 3. 还原原文
    assert restore_text_from_chunks(chunks) == text

    # 4. 全覆盖：Start/End 必须覆盖 [0, len(textRunes))
    covered = [False] * len(text_runes)
    for c in chunks:
        for p in range(c.start, min(c.end, len(text_runes))):
            covered[p] = True
    assert all(covered), "some rune position is not covered by any chunk"


def test_split_text_restore_text_with_multiple_tables() -> None:
    text = (
        "前言\n\n"
        "| A | B |\n| --- | --- |\n"
        "| 1 | 2 |\n| 3 | 4 |\n| 5 | 6 |\n| 7 | 8 |\n"
        "\n中间文字\n\n"
        "| X | Y |\n| --- | --- |\n"
        "| a | b |\n| c | d |\n| e | f |\n"
        "\n结尾"
    )

    cfg = SplitterConfig(chunk_size=50, chunk_overlap=5, separators=["\n\n", "\n"])
    chunks = split_text(text, cfg)

    assert restore_text_from_chunks(chunks) == text

    text_runes = list(text)
    for i, c in enumerate(chunks):
        content_runes = list(c.content)
        header_len = len(content_runes) - (c.end - c.start)
        assert header_len >= 0
        if c.end <= len(text_runes):
            assert "".join(text_runes[c.start : c.end]) == "".join(content_runes[header_len:])


@pytest.mark.parametrize("overlap", [0, 3, 10, 20])
def test_split_text_restore_text_with_overlap(overlap: int) -> None:
    text = (
        "| 列1 | 列2 | 列3 |\n"
        "| --- | --- | --- |\n"
        "| 数据A1 | 数据A2 | 数据A3 |\n"
        "| 数据B1 | 数据B2 | 数据B3 |\n"
        "| 数据C1 | 数据C2 | 数据C3 |\n"
        "| 数据D1 | 数据D2 | 数据D3 |\n"
        "| 数据E1 | 数据E2 | 数据E3 |\n"
        "| 数据F1 | 数据F2 | 数据F3 |\n"
        "\n"
        "表后文本。"
    )
    cfg = SplitterConfig(chunk_size=60, chunk_overlap=overlap, separators=["\n\n", "\n"])
    chunks = split_text(text, cfg)
    assert restore_text_from_chunks(chunks) == text


def test_split_text_parent_child_with_table_headers() -> None:
    text = (
        "前言\n\n"
        "| 列A | 列B |\n"
        "| --- | --- |\n"
        "| 数据1 | 数据2 |\n"
        "| 数据3 | 数据4 |\n"
        "| 数据5 | 数据6 |\n"
        "| 数据7 | 数据8 |\n"
        "\n"
        "结尾"
    )
    parent_cfg = SplitterConfig(chunk_size=200, chunk_overlap=0, separators=["\n\n", "\n"])
    child_cfg = SplitterConfig(chunk_size=40, chunk_overlap=5, separators=["\n\n", "\n"])
    result = split_text_parent_child(text, parent_cfg, child_cfg)

    assert len(result.children) > 0
    text_runes = list(text)
    for i, child in enumerate(result.children):
        assert child.start >= 0
        assert child.end <= len(text_runes)


@pytest.mark.skip(
    reason="ExtractImageRefs 依赖 WeKnora 内部包 docparser.UnwrapLinkedImages（跨包依赖），"
    "且不在本次约定的公开 API 内，未移植 —— 对应的 TestExtractImageRefs 一并跳过。"
)
def test_extract_image_refs() -> None:
    raise AssertionError("not ported")
