"""Docling floating item captions are methods in installed docling-core."""

from types import SimpleNamespace

from app.core.parsing.docling_parser import DoclingParser


def test_item_text_calls_caption_method_with_document():
    document = object()

    class Item:
        text = ""

        def caption_text(self, doc):
            assert doc is document
            return "表 1：课程安排"

    assert DoclingParser._item_text(document, Item()) == "表 1：课程安排"


def test_table_caption_is_stored_as_text(tmp_path):
    document = type("Document", (), {})()

    class Frame:
        def to_markdown(self, index):
            return "| 课程 | 学分 |"

        def to_json(self, **kwargs):
            return "[]"

    class Table:
        page = 1

        def caption_text(self, doc):
            assert doc is document
            return "学期表"

        def export_to_dataframe(self, doc):
            assert doc is document
            return Frame()

    document.tables = [Table()]
    tables = DoclingParser(images_dir=str(tmp_path))._extract_tables(document, None)
    assert len(tables) == 1
    assert tables[0].caption == "学期表"


def test_table_page_comes_from_docling_provenance(tmp_path):
    document = type("Document", (), {})()

    class Frame:
        def to_markdown(self, index):
            return "| Name | Credits |"

        def to_json(self, **kwargs):
            return "[]"

    class Table:
        # The installed Docling TableItem has prov, but no page field.
        prov = [SimpleNamespace(page_no=2)]

        def caption_text(self, doc):
            return ""

        def export_to_dataframe(self, doc):
            return Frame()

    document.tables = [Table()]
    tables = DoclingParser(images_dir=str(tmp_path))._extract_tables(document, None)
    assert len(tables) == 1
    assert tables[0].page == 2
