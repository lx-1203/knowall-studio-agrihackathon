"""A PDF page can contain both searchable prose and scanned text."""

import pytest
from PIL import Image, ImageDraw

from app.core.parsing import DocumentParser


def test_small_logo_does_not_expand_partial_scan_crop(tmp_path):
    import fitz
    from app.core.parsing.scan_pdf import partial_scan_bbox

    large = tmp_path / "panel.png"
    logo = tmp_path / "logo.png"
    Image.new("RGB", (400, 300), "white").save(large)
    Image.new("RGB", (20, 20), "white").save(logo)
    pdf = tmp_path / "with_logo.pdf"
    with fitz.open() as document:
        page = document.new_page(width=400, height=600)
        page.insert_image(fitz.Rect(0, 300, 400, 600), filename=str(large))
        page.insert_image(fitz.Rect(350, 20, 370, 40), filename=str(logo))
        document.save(pdf)
    with fitz.open(pdf) as document:
        assert partial_scan_bbox(document[0]).y0 == 300


@pytest.mark.asyncio
async def test_half_page_scan_preserves_both_text_sources_without_docling(monkeypatch, tmp_path):
    import fitz

    from app.core.parsing import local_ocr, settings

    picture = tmp_path / "scanned_section.png"
    image = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(image).text((20, 40), "SCANNED CONTENT ONLY", fill="black")
    image.save(picture)

    pdf = tmp_path / "mixed_within_page.pdf"
    with fitz.open() as document:
        page = document.new_page(width=400, height=600)
        page.insert_textbox(fitz.Rect(20, 30, 380, 270), "DIGITAL PROSE " * 30)
        page.insert_image(fitz.Rect(0, 300, 400, 600), filename=str(picture))
        document.save(pdf)

    monkeypatch.setattr(settings, "use_docling", False)
    ocr_calls = []

    def recognize_scan(image):
        ocr_calls.append(image.size)
        return local_ocr.OCRPage([
            local_ocr.OCRLine("SCANNED CONTENT ONLY", (20, 40, 220, 60), 0.99),
        ], 0.01)

    monkeypatch.setattr(local_ocr, "recognize", recognize_scan)
    parsed = await DocumentParser().parse(str(pdf), "pdf")

    assert ocr_calls
    assert "DIGITAL PROSE" in parsed.text
    assert "SCANNED CONTENT ONLY" in parsed.text
    assert parsed.page_count == 1
    assert any("DIGITAL PROSE" in text and page == 1 for text, page in parsed.page_items)
    assert any("SCANNED CONTENT ONLY" in text and page == 1 for text, page in parsed.page_items)
