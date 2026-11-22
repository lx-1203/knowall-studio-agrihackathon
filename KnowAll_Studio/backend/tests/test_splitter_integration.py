"""TextSplitter 集成层单测：面包屑去重 + 真实页码 + 结构保护。

分块引擎本身的行为由 tests/test_chunking_*.py 覆盖（Go 单测逐条翻译）。
这里只测集成层新增的三件事 —— 它们才是"接进项目"后真正影响检索质量的部分。
"""
from __future__ import annotations

import pytest

from app.core.parsing import TextSplitter
from app.core.parsing.page_map import PageSpan, PageMap

SHORT_DOC = "正文内容够长就能切出来。\n\n第二段也要够长才行。"


class TestBreadcrumbMerge:
    """面包屑必须补进正文，但不能和正文已有的标题行重复。"""

    DOC = (
        "# 第一章 绪论\n\n"
        "开篇介绍本章范围。\n\n"
        "## 1.1 背景\n\n"
        "背景段落讲的是资料散落各处的问题，长度足够撑起一个独立分块。\n\n"
        "## 1.2 目标\n\n"
        "目标段落同样要写得足够长，保证分块引擎会为它单独产出一块内容。\n"
    )

    def test_parent_breadcrumb_is_prepended(self):
        spell = TextSplitter(chunk_size_tokens=40, strategy="heading")
        chunks = spell.split(self.DOC)
        with_header = [c for c in chunks if c.context_header]
        assert with_header, "应有 chunk 拿到面包屑"

        deep = [c for c in with_header if "1.1" in c.context_header]
        assert deep, "1.1 所属的 chunk 应该带两级面包屑"
        parent = deep[0].context_header.splitlines()[0]
        assert parent.startswith("# 第一章")
        assert parent in deep[0].text, "父级标题正文里没有，必须补进去"

    def test_no_duplicate_heading_lines_in_merged_text(self):
        spell = TextSplitter(chunk_size_tokens=40, strategy="heading")
        for c in spell.split(self.DOC):
            heading_lines = [
                ln.strip() for ln in c.text.splitlines() if ln.lstrip().startswith("#")
            ]
            assert len(heading_lines) == len(set(heading_lines)), (
                f"chunk {c.index} 的标题行重复了: {heading_lines}"
            )


class TestPageRangeResolution:
    """页码要来自 page_map；拿不到就留空，不许编。"""

    def test_real_pages_are_used(self):
        page1 = "第一页的正文内容在这里，需要足够长才能单独成块。"
        page2 = "第二页的正文内容在这里，同样需要足够长才能单独成块。"
        text = page1 + "\n\n" + page2
        boundary = len(page1) + 2
        page_map = PageMap(
            [PageSpan(0, boundary, 1), PageSpan(boundary, len(text), 2)]
        )
        # 预算压到 12 token（≈18 字符），保证两页各自切成多块
        chunks = TextSplitter(chunk_size_tokens=12).split(
            text, total_pages=2, page_map=page_map
        )
        assert len(chunks) > 1, [(c.page_range, c.text) for c in chunks]
        ranges = {c.page_range for c in chunks}
        assert ranges <= {"p1", "p2", "p1-p2"}, ranges

        first = [c for c in chunks if "第一页" in c.text]
        second = [c for c in chunks if "第二页" in c.text]
        assert first and all(c.page_range.startswith("p1") for c in first), [
            (c.page_range, c.text) for c in first
        ]
        assert second and all("2" in c.page_range for c in second), [
            (c.page_range, c.text) for c in second
        ]

    def test_unknown_pages_are_blank_for_multipage_document(self):
        spell = TextSplitter(chunk_size_tokens=20)
        chunks = spell.split(SHORT_DOC, total_pages=9)
        # 没有 page_map 又有多页：宁可不显示，也不能编一个页码
        assert chunks and all(c.page_range == "" for c in chunks)

    def test_single_page_document_reports_p1(self):
        spell = TextSplitter(chunk_size_tokens=20)
        chunks = spell.split(SHORT_DOC, total_pages=1)
        assert chunks and all(c.page_range == "p1" for c in chunks)


class TestStructureProtection:
    """表格和代码块不能被从中间切断 —— 切碎了这两样就失去意义。"""

    DOC = (
        "# 实验方法\n\n"
        "先用下表对齐几个工具的差异：\n\n"
        "| 工具 | 检索方式 | 中文支持 |\n"
        "| --- | --- | --- |\n"
        "| A 工具 | 文件名匹配 | 否 |\n"
        "| B 工具 | 全文检索 | 部分 |\n"
        "| C 工具 | 向量检索 | 是 |\n\n"
        "然后是调用示例：\n\n"
        '```python\n'
        "def retrieve(query: str) -> list[str]:\n"
        "    return vector_search(query) + bm25_search(query)\n"
        '```\n\n'
        "最后补充一段说明，长度足够让它自己单独成为一块，避免结论被表或代码吸走。\n"
    )

    # 预算 = token 目标 × 2.7 字符（mixed 语种），80 token ≈ 216 字符，
    # 刚好容得下这张 110 字符的表，同时整篇仍会被切成 2 块。
    TABLE_BUDGET_TOKENS = 80
    # 30 token ≈ 81 字符，容不下代码块之外的任何结构，用来逼出多块。
    TIGHT_TOKENS = 30

    def test_table_not_split_across_chunks(self):
        chunks = TextSplitter(chunk_size_tokens=self.TABLE_BUDGET_TOKENS).split(self.DOC)
        assert len(chunks) > 1, "预算调大了也要保证确实切成了多块，否则这条测试是空转"
        assert any(
            "| A 工具 | 文件名匹配 | 否 |" in c.text
            and "| C 工具 | 向量检索 | 是 |" in c.text
            for c in chunks
        ), [c.text[:40] for c in chunks]

    def test_code_block_within_budget_not_split_across_chunks(self):
        # Structure stays intact when it fits the configured embedding budget.
        # Oversized protection is tested separately in test_chunking_protected_spans.
        chunks = TextSplitter(chunk_size_tokens=self.TABLE_BUDGET_TOKENS).split(self.DOC)
        assert len(chunks) > 1
        assert any("def retrieve" in c.text and "bm25_search" in c.text for c in chunks)


class TestDocxHeadingLevels:
    """Word 的标题层级必须转成 Markdown 井号。

    防的是这个毛病：解析器只取段落纯文本、把"这是标题"这个信息扔掉，
    于是分块引擎完全看不出文档结构，切出来的每段都没有出处。
    """

    @staticmethod
    def _fake_paragraph(style_name: str):
        """伪造一个只有样式名的段落对象。

        为什么不用真的 Word：中文版 Word 的样式名是"标题 2"，而 python-docx
        的 add_heading() 只会生成英文名"Heading 2"。要覆盖中文分支，只能伪造。
        """

        class _Style:
            name = style_name

        class _Paragraph:
            style = _Style()

        return _Paragraph()

    def test_heading_styles_map_to_hashes(self, tmp_path):
        import docx as docxlib

        from app.core.parsing import DocumentParser

        d = docxlib.Document()
        d.add_heading("第一章", level=1)
        d.add_heading("1.1 小节", level=2)
        d.add_heading("1.1.1 细节", level=3)
        d.add_paragraph("这是普通正文，不是标题")
        levels = [DocumentParser._docx_heading_level(p) for p in d.paragraphs]
        assert levels == [1, 2, 3, 0]

    def test_chinese_locale_style_names_are_recognized(self):
        from app.core.parsing import DocumentParser

        assert DocumentParser._docx_heading_level(self._fake_paragraph("标题 2")) == 2
        assert DocumentParser._docx_heading_level(self._fake_paragraph("Heading 4")) == 4
        assert DocumentParser._docx_heading_level(self._fake_paragraph("Title")) == 1
        assert DocumentParser._docx_heading_level(self._fake_paragraph("正文")) == 0
        # 超过 6 级要夹到 6，因为 Markdown 只有 6 个井号
        assert DocumentParser._docx_heading_level(self._fake_paragraph("Heading 9")) == 6

    def test_parsed_docx_keeps_hashes_end_to_end(self, tmp_path, monkeypatch):
        import asyncio

        import docx as docxlib

        from app.config import settings
        from app.core.parsing import parser

        # 强制走兜底解析器，避免测试去下载 docling 的模型
        monkeypatch.setattr(settings, "use_docling", False)

        d = docxlib.Document()
        d.add_heading("第一章 机器学习基础", level=1)
        d.add_paragraph("正文内容。")
        path = tmp_path / "headings.docx"
        d.save(str(path))

        parsed = asyncio.run(parser.parse(str(path), "docx"))
        assert "# 第一章 机器学习基础" in parsed.text


def test_empty_text_yields_no_chunks():
    assert TextSplitter().split("") == []
    assert TextSplitter().split("   \n\n  ") == []


def test_chunk_offsets_are_within_text():
    text = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
    for c in TextSplitter(chunk_size_tokens=15).split(text):
        assert 0 <= c.start < c.end <= len(text)
