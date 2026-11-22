"""Exercise the document formats through the public parser entry point."""

import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.documents import upload_document
from app.config import settings
from app.core.parsing import DocumentParser
from app.core.parsing.docreader import epub_parser
from app.database import Base
from app.models import Document, DocumentChunk


def _epub_bytes() -> bytes:
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/book.opf"/></rootfiles></container>',
        )
        archive.writestr(
            "OEBPS/book.opf",
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>网络教材</dc:title></metadata>'
            '<manifest><item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/>'
            '<item id="one" href="one.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="two" href="two.xhtml" media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="one"/><itemref idref="two"/></spine></package>',
        )
        archive.writestr(
            "OEBPS/nav.xhtml",
            '<html><body><nav><a href="one.xhtml">第一章 网络基础</a>'
            '<a href="two.xhtml">第二章 传输层</a></nav></body></html>',
        )
        archive.writestr(
            "OEBPS/one.xhtml",
            '<html><body><h1>第一章 网络基础</h1><h2>1.1 协议</h2>'
            '<p>协议规定设备之间如何交换数据。</p></body></html>',
        )
        archive.writestr(
            "OEBPS/two.xhtml",
            '<html><body><h1>第二章 传输层</h1><p>TCP 提供可靠传输。</p></body></html>',
        )
    return data.getvalue()


@pytest.mark.asyncio
async def test_epub_keeps_chapters_and_section_headings(tmp_path):
    path = tmp_path / "textbook.epub"
    path.write_bytes(_epub_bytes())

    parsed = await DocumentParser().parse(str(path), "epub")

    assert parsed.metadata["parser"] == "docreader.epub"
    assert parsed.metadata["title"] == "网络教材"
    assert "# 第一章 网络基础" in parsed.text
    assert "## 1.1 协议" in parsed.text
    assert "# 第二章 传输层" in parsed.text
    assert parsed.text.index("协议规定") < parsed.text.index("TCP 提供")


def test_epub_spine_order_survives_sparse_table_of_contents():
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>',
        )
        archive.writestr(
            "book.opf",
            '<package><manifest>'
            '<item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/>'
            '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="b" href="b.xhtml" media-type="application/xhtml+xml"/>'
            '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/>'
            '</manifest><spine><itemref idref="a"/><itemref idref="b"/><itemref idref="c"/>'
            '</spine></package>',
        )
        archive.writestr(
            "nav.xhtml",
            '<html><body><nav><a href="a.xhtml">Chapter A</a>'
            '<a href="c.xhtml">Chapter C</a></nav></body></html>',
        )
        for name in ("a", "b", "c"):
            archive.writestr(
                f"{name}.xhtml",
                f"<html><body><h1>Chapter {name.upper()}</h1><p>Body {name}</p></body></html>",
            )

    parsed = epub_parser.parse_epub(data.getvalue(), extract_images=False)
    assert parsed.content.index("Body a") < parsed.content.index("Body b") < parsed.content.index("Body c")


@pytest.mark.asyncio
@pytest.mark.parametrize("extension", ["mhtml", "mht"])
async def test_mhtml_extracts_main_article_with_heading(tmp_path, extension):
    message = MIMEMultipart("related")
    article = MIMEText(
        "<html><body><h1>课程笔记</h1><p>这是关于数据结构的完整学习资料。</p></body></html>",
        "html",
        "utf-8",
    )
    article.add_header("Content-Location", "https://example.com/course")
    message.attach(article)
    path = tmp_path / f"course.{extension}"
    path.write_bytes(message.as_bytes())

    parsed = await DocumentParser().parse(str(path), extension)

    assert parsed.metadata["parser"] == "docreader.mhtml"
    assert "# 课程笔记" in parsed.text
    assert "数据结构" in parsed.text


@pytest.mark.asyncio
async def test_xmind_keeps_root_and_nested_topics(tmp_path):
    path = tmp_path / "course.xmind"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "content.json",
            json.dumps([
                {
                    "id": "sheet-1",
                    "title": "课程地图",
                    "rootTopic": {
                        "id": "root",
                        "title": "计算机网络",
                        "children": {"attached": [
                            {"id": "child", "title": "传输层", "children": {"attached": [
                                {"id": "leaf", "title": "TCP 三次握手"}
                            ]}}
                        ]},
                    },
                }
            ], ensure_ascii=False),
        )

    parsed = await DocumentParser().parse(str(path), "xmind")

    assert "# 计算机网络" in parsed.text
    assert "## 传输层" in parsed.text
    assert "### TCP 三次握手" in parsed.text


def test_epub_rejects_oversized_expanded_member(monkeypatch):
    # A tiny ZIP can expand to a very large XHTML chapter; reject it before reading.
    monkeypatch.setattr(epub_parser, "_MAX_MEMBER_UNCOMPRESSED_BYTES", 1024, raising=False)
    archive_bytes = BytesIO()
    with ZipFile(archive_bytes, "w") as archive:
        archive.writestr("chapter.xhtml", "<p>" + "A" * 2048 + "</p>")

    with pytest.raises(ValueError, match="uncompressed"):
        epub_parser.parse_epub(archive_bytes.getvalue(), extract_images=False)


@pytest.mark.asyncio
async def test_epub_upload_persists_document_and_search_chunks(tmp_path, monkeypatch):
    from app.core.glossary import extractor

    async def skip_glossary(*args, **kwargs):
        return None

    monkeypatch.setattr(settings, "document_dir", str(tmp_path))
    monkeypatch.setattr(settings, "kaf_publish_enabled", False)
    monkeypatch.setattr(extractor, "sync_glossary_from_db", skip_glossary)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            response = await upload_document(
                UploadFile(file=BytesIO(_epub_bytes()), filename="textbook.epub"),
                db=session,
            )
            stored = (await session.execute(
                select(Document).where(Document.id == response["document_id"])
            )).scalar_one()
            chunks = (await session.execute(
                select(DocumentChunk)
                .where(DocumentChunk.doc_id == stored.id)
                .order_by(DocumentChunk.chunk_index)
            )).scalars().all()
    finally:
        await engine.dispose()

    assert response["file_type"] == "epub"
    assert stored.metadata_["parser"] == "docreader.epub"
    assert chunks
    assert any("协议规定" in chunk.text_content for chunk in chunks)
    assert any("第一章 网络基础" in chunk.text_content for chunk in chunks)


@pytest.mark.asyncio
async def test_upload_without_reported_size_stops_at_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "document_dir", str(tmp_path))
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    upload = UploadFile(file=BytesIO(b"x" * (1024 * 1024 + 1)), filename="large.epub")
    assert upload.size is None

    with pytest.raises(HTTPException, match="File too large") as excinfo:
        await upload_document(upload, db=None)

    assert excinfo.value.status_code == 400
    assert list(tmp_path.iterdir()) == []
