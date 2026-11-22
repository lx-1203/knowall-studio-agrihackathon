"""PDF scan detection must preserve image content on mixed pages."""

import asyncio
import time
import pytest
from PIL import Image, ImageDraw

from app.core.parsing import DocumentParser, ParsedDocument


@pytest.mark.asyncio
async def test_pdf_inspection_and_rendering_do_not_block_event_loop(monkeypatch, tmp_path):
    import fitz
    from app.core.parsing import settings, scan_pdf

    pdf = tmp_path / "digital.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Digital page text")
    doc.save(pdf)
    doc.close()
    monkeypatch.setattr(settings, "use_docling", False)

    def slow_classify(page):
        time.sleep(0.2)
        return False

    monkeypatch.setattr(scan_pdf, "page_needs_ocr", slow_classify)
    task = asyncio.create_task(DocumentParser().parse(str(pdf), "pdf"))
    started = time.perf_counter()
    await asyncio.sleep(0.04)
    elapsed = time.perf_counter() - started
    await task
    assert elapsed < 0.15


@pytest.mark.asyncio
async def test_full_page_image_with_small_text_layer_is_ocrd(monkeypatch, tmp_path):
    import fitz
    from app.core.parsing import local_ocr

    picture = tmp_path / "page.png"
    Image.new("RGB", (400, 600), "white").save(picture)
    pdf = tmp_path / "hybrid.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    page.insert_image(page.rect, filename=str(picture))
    page.insert_text((20, 20), "1")
    doc.save(pdf)
    doc.close()

    calls = []

    def fake_ocr(image):
        calls.append(image.size)
        return local_ocr.OCRPage([local_ocr.OCRLine("完整扫描正文", (10, 10, 100, 30), 0.99)], 0.1)

    monkeypatch.setattr(local_ocr, "recognize", fake_ocr)
    parsed = await DocumentParser().parse(str(pdf), "pdf")
    assert len(calls) == 1
    assert parsed.text == "完整扫描正文"
    assert parsed.page_items == [("完整扫描正文", 1)]


def test_small_logo_keeps_text_layer(tmp_path):
    import fitz
    from app.core.parsing.scan_pdf import page_needs_ocr

    picture = tmp_path / "logo.png"
    Image.new("RGB", (40, 40), "white").save(picture)
    pdf = tmp_path / "digital.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    page.insert_image(fitz.Rect(20, 20, 60, 60), filename=str(picture))
    page.insert_text((20, 100), "A page with searchable digital prose")
    doc.save(pdf)
    doc.close()
    with fitz.open(pdf) as opened:
        assert not page_needs_ocr(opened[0])


def test_full_page_scan_with_partial_text_layer_still_needs_ocr(tmp_path):
    import fitz
    from app.core.parsing.scan_pdf import page_needs_ocr

    picture = tmp_path / "page.png"
    Image.new("RGB", (400, 600), "white").save(picture)
    pdf = tmp_path / "partial.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    page.insert_image(page.rect, filename=str(picture))
    page.insert_textbox(fitz.Rect(20, 20, 380, 400), "Partial searchable text. " * 8)
    doc.save(pdf)
    doc.close()
    with fitz.open(pdf) as opened:
        assert len(opened[0].get_text().strip()) >= 80
        assert page_needs_ocr(opened[0])


def test_tiled_scan_images_are_combined_for_coverage(tmp_path):
    import fitz
    from app.core.parsing.scan_pdf import page_needs_ocr

    picture = tmp_path / "tile.png"
    Image.new("RGB", (200, 300), "white").save(picture)
    pdf = tmp_path / "tiled.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    for left in (0, 200):
        for top in (0, 300):
            page.insert_image(fitz.Rect(left, top, left + 200, top + 300), filename=str(picture))
    page.insert_text((20, 20), "1")
    doc.save(pdf)
    doc.close()
    with fitz.open(pdf) as opened:
        assert page_needs_ocr(opened[0])


def test_vector_only_page_without_text_layer_needs_ocr(tmp_path):
    import fitz
    from app.core.parsing.scan_pdf import page_needs_ocr

    pdf = tmp_path / "outlined.pdf"
    doc = fitz.open()
    page = doc.new_page()
    for offset in range(20):
        page.draw_line((30, 30 + offset * 8), (190, 30 + offset * 8))
    doc.save(pdf)
    doc.close()
    with fitz.open(pdf) as opened:
        assert not opened[0].get_text().strip()
        assert not opened[0].get_image_info()
        assert page_needs_ocr(opened[0])


@pytest.mark.asyncio
async def test_two_scanned_pages_keep_order_and_page_numbers(monkeypatch, tmp_path):
    import fitz
    from app.core.parsing import local_ocr

    picture = tmp_path / "scan.png"
    Image.new("RGB", (80, 40), "white").save(picture)
    pdf = tmp_path / "scans.pdf"
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page(width=200, height=100)
        page.insert_image(page.rect, filename=str(picture))
    doc.save(pdf)
    doc.close()

    calls = []

    def fake_ocr(image):
        calls.append(image.size)
        number = len(calls)
        return local_ocr.OCRPage([local_ocr.OCRLine(f"第{number}页", (2, 2, 70, 20), 0.99)], 0.1)

    monkeypatch.setattr(local_ocr, "recognize", fake_ocr)
    parsed = await DocumentParser().parse(str(pdf), "pdf")
    assert len(calls) == 2
    assert parsed.page_count == 2
    assert parsed.text == "第1页\n\n第2页"
    assert parsed.page_items == [("第1页", 1), ("第2页", 2)]


@pytest.mark.asyncio
async def test_ocr_failure_reports_pdf_page(monkeypatch, tmp_path):
    import fitz
    from app.core.parsing import local_ocr

    picture = tmp_path / "scan.png"
    Image.new("RGB", (80, 40), "white").save(picture)
    pdf = tmp_path / "scan.pdf"
    doc = fitz.open()
    doc.new_page().insert_image(fitz.Rect(20, 20, 100, 60), filename=str(picture))
    doc.save(pdf)
    doc.close()
    monkeypatch.setattr(local_ocr, "recognize", lambda image: (_ for _ in ()).throw(RuntimeError("model missing")))
    with pytest.raises(ValueError, match="PDF page 1.*model missing"):
        await DocumentParser().parse(str(pdf), "pdf")


@pytest.mark.asyncio
async def test_empty_ocr_result_reports_pdf_page(monkeypatch, tmp_path):
    import fitz
    from app.core.parsing import local_ocr

    picture = tmp_path / "scan.png"
    image = Image.new("RGB", (80, 40), "white")
    ImageDraw.Draw(image).rectangle((10, 10, 60, 25), fill="black")
    image.save(picture)
    pdf = tmp_path / "scan.pdf"
    doc = fitz.open()
    doc.new_page().insert_image(fitz.Rect(20, 20, 100, 60), filename=str(picture))
    doc.save(pdf)
    doc.close()
    monkeypatch.setattr(local_ocr, "recognize", lambda image: local_ocr.OCRPage([], 0.1))
    with pytest.raises(ValueError, match="PDF page 1.*no text"):
        await DocumentParser().parse(str(pdf), "pdf")


@pytest.mark.asyncio
async def test_blank_scanned_back_page_does_not_fail_document(monkeypatch, tmp_path):
    import fitz
    from app.core.parsing import local_ocr

    picture = tmp_path / "blank.png"
    Image.new("RGB", (80, 40), "white").save(picture)
    pdf = tmp_path / "with_blank.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "First page text")
    doc.new_page().insert_image(fitz.Rect(20, 20, 100, 60), filename=str(picture))
    doc.save(pdf)
    doc.close()
    from app.core.parsing import settings
    monkeypatch.setattr(settings, "use_docling", False)
    monkeypatch.setattr(local_ocr, "recognize", lambda image: local_ocr.OCRPage([], 0.1))
    parsed = await DocumentParser().parse(str(pdf), "pdf")
    assert parsed.page_count == 2
    assert parsed.page_items == [("First page text\n", 1)]


@pytest.mark.asyncio
async def test_all_blank_scanned_pdf_reports_no_text(monkeypatch, tmp_path):
    import fitz
    from app.core.parsing import local_ocr

    picture = tmp_path / "blank.png"
    Image.new("RGB", (80, 40), "white").save(picture)
    pdf = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page().insert_image(fitz.Rect(20, 20, 100, 60), filename=str(picture))
    doc.save(pdf)
    doc.close()
    monkeypatch.setattr(local_ocr, "recognize", lambda image: local_ocr.OCRPage([], 0.1))
    with pytest.raises(ValueError, match="no text recognized in PDF"):
        await DocumentParser().parse(str(pdf), "pdf")


@pytest.mark.asyncio
async def test_illustration_only_page_does_not_discard_other_pages(monkeypatch, tmp_path):
    import fitz
    from app.core.parsing import local_ocr, settings

    picture = tmp_path / "illustration.png"
    image = Image.new("RGB", (80, 40), "white")
    ImageDraw.Draw(image).ellipse((10, 5, 65, 35), fill="black")
    image.save(picture)
    pdf = tmp_path / "illustrated.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Useful chapter text")
    doc.new_page().insert_image(fitz.Rect(20, 20, 100, 60), filename=str(picture))
    doc.save(pdf)
    doc.close()
    monkeypatch.setattr(settings, "use_docling", False)
    monkeypatch.setattr(local_ocr, "recognize", lambda image: local_ocr.OCRPage([], 0.1))
    parsed = await DocumentParser().parse(str(pdf), "pdf")
    assert "Useful chapter text" in parsed.text
    assert parsed.page_count == 2
    assert parsed.metadata["unrecognized_pages"] == [2]


@pytest.mark.asyncio
async def test_mixed_pdf_uses_docling_markdown_for_digital_page(monkeypatch, tmp_path):
    import fitz
    from app.core.parsing import local_ocr, settings

    picture = tmp_path / "scan.png"
    Image.new("RGB", (80, 40), "white").save(picture)
    pdf = tmp_path / "mixed.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Digital title")
    doc.new_page().insert_image(fitz.Rect(20, 20, 100, 60), filename=str(picture))
    doc.save(pdf)
    doc.close()
    marker = object()
    calls = []

    async def fake_docling(self, path, file_type):
        calls.append((path, file_type))
        return ParsedDocument(
            text="# Digital title\n\n| A | B |", page_count=2,
            metadata={"parser": "docling"},
            tables=[marker],
            page_texts=["# Digital title\n\n| A | B |", ""],
        )

    monkeypatch.setattr(settings, "use_docling", True)
    monkeypatch.setattr(DocumentParser, "_parse_with_docling", fake_docling)
    monkeypatch.setattr(local_ocr, "recognize", lambda image: local_ocr.OCRPage([
        local_ocr.OCRLine("扫描内容", (2, 2, 70, 20), 0.99)
    ], 0.1))
    parsed = await DocumentParser().parse(str(pdf), "pdf")
    assert calls == [(str(pdf), "pdf")]
    assert parsed.text == "# Digital title\n\n| A | B |\n\n扫描内容"
    assert parsed.tables == [marker]
    assert parsed.page_items == [
        ("# Digital title\n\n| A | B |", 1), ("扫描内容", 2)
    ]
