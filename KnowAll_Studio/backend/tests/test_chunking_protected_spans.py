"""protected_spans_test.go 的逐条翻译。"""

from __future__ import annotations

from app.core.parsing.chunking import (
    Chunk,
    STRATEGY_LEGACY,
    SplitterConfig,
    protected_spans,
    protected_spans_rune,
    split,
    split_text,
)


def build_ocr_stray_bracket_doc() -> str:
    """构造本次回归针对的 OCR 失败模式：全角「【公章】」的左括号被误读成
    半角 '[', 后面又出现一段无关的半角 "](...)" 片段。

    CommonMark 不允许链接文本/目标跨空行，但修复前那条无界的
    链接模式把游离的 '[' 和远处的 '](' 串起来，把整篇文档吞成一个
    受保护的原子区。
    """
    parts = ["[公章】\n\n"]
    for i in range(60):
        parts.append(f"这是第{i}段用于测试切分的中文内容，包含常见标点符号。\n\n")
    parts.append("第三段（北京)\n\n")
    parts.append("[引用](来源)")
    return "".join(parts)


def max_chunk_runes(chunks: list[Chunk]) -> int:
    return max((len(c.content) for c in chunks), default=0)


def test_split_text_ocr_stray_bracket_does_not_swallow_document() -> None:
    doc = build_ocr_stray_bracket_doc()
    assert "[公章】" in doc and "](来源)" in doc

    chunk_size = 128
    cfg = SplitterConfig(chunk_size=chunk_size, chunk_overlap=0, separators=["\n\n", "\n", "。"])

    chunks = split_text(doc, cfg)
    assert len(chunks) >= 2
    assert max_chunk_runes(chunks) <= 2 * chunk_size


def test_split_ocr_stray_bracket_does_not_swallow_document() -> None:
    doc = build_ocr_stray_bracket_doc()
    chunk_size = 128
    cfg = SplitterConfig(
        chunk_size=chunk_size,
        chunk_overlap=0,
        separators=["\n\n", "\n", "。"],
        strategy=STRATEGY_LEGACY,
    )

    chunks = split(doc, cfg)
    assert len(chunks) >= 2
    assert max_chunk_runes(chunks) <= 2 * chunk_size


def test_protected_spans_no_cross_line_link_or_image() -> None:
    doc = build_ocr_stray_bracket_doc()
    # Go 里 protectedSpans 给的是字节偏移，直接用字节下标切 Go 字符串没问题；
    # Python 必须先把偏移转成字符偏移才能切片，所以这里用 rune 版（指向同一批
    # 字符），断言语义与 Go 完全一致。
    for s in protected_spans_rune(doc, protected_spans(doc)):
        seg = doc[s.start : s.end]
        assert "\n" not in seg, f"protected span spans a newline: {seg!r}"


def test_protected_spans_markdown_links_and_images_still_protected() -> None:
    cases = [
        "![alt](http://x/y.png)",
        "[文字](http://x.com)",
        '[link](url "title")',
        "![](resource://abc)",
    ]
    for c in cases:
        spans = protected_spans(c)
        assert spans, f"expected {c!r} to be protected, got no span"
        # Go 的 len(c) 是字节长度，protectedSpans 给的也是字节偏移，
        # 所以这里必须用字节长度对齐（不能写 len(c)：那是字符长度）。
        assert (spans[0].start, spans[0].end) == (0, len(c.encode("utf-8")))


def test_protected_spans_multiline_link_intentionally_unprotected() -> None:
    # CommonMark 允许链接文本里有一个软换行，但受保护模式仍然拒绝它，
    # 免得 OCR 残留的 '[' 吞掉整段。
    doc = "[link\ntext](http://example.com)"
    assert protected_spans(doc) == []


def test_protected_spans_literal_cross_line_example() -> None:
    doc = "[公章】\n\n第一段。\n\n第二段。\n\n第三段（北京)"
    assert protected_spans(doc) == []


def test_oversized_protected_code_block_obeys_chunk_budget() -> None:
    text = "```text\n" + "x" * 900 + "\n```"
    cfg = SplitterConfig(chunk_size=300, chunk_overlap=0, separators=["\n"])

    chunks = split_text(text, cfg)

    assert len(chunks) > 1
    assert all(len(chunk.content) <= 300 for chunk in chunks)
    assert "".join(chunk.content for chunk in chunks) == text
    assert all(chunk.content == text[chunk.start:chunk.end] for chunk in chunks)
