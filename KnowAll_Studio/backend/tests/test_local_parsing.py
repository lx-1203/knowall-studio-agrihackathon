"""Local OCR integration and structure recovery."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import pytest
from PIL import Image, ImageDraw

from app.core.parsing import DocumentParser


@pytest.mark.asyncio
async def test_image_uses_local_ocr_and_keeps_source(monkeypatch, tmp_path):
    from app.core.parsing import local_ocr

    path = tmp_path / "page.png"
    Image.new("RGB", (80, 40), "white").save(path)
    calls = []

    def fake_ocr(image):
        calls.append(image.size)
        return local_ocr.OCRPage([local_ocr.OCRLine("第一行", (2, 2, 60, 20), 0.99)], 0.01)

    monkeypatch.setattr(local_ocr, "recognize", fake_ocr)
    parsed = await DocumentParser().parse(str(path), "png")
    assert calls == [(80, 40)]
    assert parsed.text == "第一行"
    assert parsed.page_items == [("第一行", 1)]
    assert parsed.metadata["parser"] == "RapidOCR"


@pytest.mark.asyncio
async def test_mixed_pdf_preserves_scanned_page(monkeypatch, tmp_path):
    import fitz
    from app.core.parsing import local_ocr, settings

    path = tmp_path / "mixed.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Digital page text")
    image_path = tmp_path / "scan.png"
    Image.new("RGB", (80, 40), "white").save(image_path)
    doc.new_page().insert_image(fitz.Rect(40, 40, 120, 80), filename=str(image_path))
    doc.save(path)
    doc.close()

    calls = []

    def fake_ocr(image):
        calls.append(image.size)
        return local_ocr.OCRPage([local_ocr.OCRLine("扫描页文字", (2, 2, 70, 20), 0.99)], 0.02)

    monkeypatch.setattr(settings, "use_docling", False)
    monkeypatch.setattr(local_ocr, "recognize", fake_ocr)
    parsed = await DocumentParser().parse(str(path), "pdf")
    assert len(calls) == 1
    assert parsed.page_count == 2
    assert parsed.page_items == [("Digital page text\n", 1), ("扫描页文字", 2)]
    assert "扫描页文字" in parsed.text


def test_grid_returns_cells_and_escapes_markdown():
    from app.core.parsing.local_ocr import OCRLine, OCRPage
    from app.core.parsing.table_grid import recover_table

    image = Image.new("RGB", (240, 120), "white")
    draw = ImageDraw.Draw(image)
    for x in (10, 110, 230):
        draw.line((x, 10, x, 110), fill="black", width=2)
    for y in (10, 60, 110):
        draw.line((10, y, 230, y), fill="black", width=2)
    lines = [
        OCRLine("甲|乙", (20, 20, 80, 40), 0.99),
        OCRLine("学分", (130, 20, 190, 40), 0.99),
        OCRLine("课程", (20, 70, 80, 90), 0.99),
        OCRLine("5", (130, 70, 140, 90), 0.99),
    ]
    result = recover_table(image, OCRPage(lines, 0))
    assert result is not None
    assert result.cells == [["甲|乙", "学分"], ["课程", "5"]]
    assert "甲\\|乙" in result.markdown
    assert "| --- | --- |" in result.markdown


def test_grid_rejects_unruled_text():
    from app.core.parsing.local_ocr import OCRPage
    from app.core.parsing.table_grid import recover_table

    assert recover_table(Image.new("RGB", (240, 120), "white"), OCRPage([], 0)) is None


@pytest.mark.asyncio
async def test_image_table_preserves_text_outside_grid(monkeypatch, tmp_path):
    from app.core.parsing import local_ocr

    image = Image.new("RGB", (240, 160), "white")
    draw = ImageDraw.Draw(image)
    for x in (10, 110, 230):
        draw.line((x, 30, x, 120), fill="black", width=2)
    for y in (30, 75, 120):
        draw.line((10, y, 230, y), fill="black", width=2)
    path = tmp_path / "table.png"
    image.save(path)
    lines = [
        local_ocr.OCRLine("表格标题", (20, 3, 100, 20), 0.99),
        local_ocr.OCRLine("课程", (20, 40, 70, 60), 0.99),
        local_ocr.OCRLine("学分", (120, 40, 170, 60), 0.99),
        local_ocr.OCRLine("数学", (20, 85, 70, 105), 0.99),
        local_ocr.OCRLine("4", (120, 85, 130, 105), 0.99),
        local_ocr.OCRLine("表格注释", (20, 135, 100, 150), 0.99),
    ]
    monkeypatch.setattr(local_ocr, "recognize", lambda image: local_ocr.OCRPage(lines, 0.1))
    parsed = await DocumentParser().parse(str(path), "png")
    assert parsed.text.startswith("表格标题\n")
    assert "| 课程 | 学分 |" in parsed.text
    assert parsed.text.endswith("\n表格注释")


@pytest.mark.asyncio
async def test_image_ocr_does_not_block_event_loop(monkeypatch, tmp_path):
    from app.core.parsing import local_ocr

    path = tmp_path / "page.png"
    Image.new("RGB", (80, 40), "white").save(path)

    def slow_ocr(image):
        time.sleep(0.2)
        return local_ocr.OCRPage([local_ocr.OCRLine("文字", (2, 2, 60, 20), 0.99)], 0.2)

    monkeypatch.setattr(local_ocr, "recognize", slow_ocr)
    parse_task = asyncio.create_task(DocumentParser().parse(str(path), "png"))
    started = time.perf_counter()
    await asyncio.sleep(0.04)
    elapsed = time.perf_counter() - started
    await parse_task
    assert elapsed < 0.15


@pytest.mark.asyncio
async def test_image_decode_does_not_block_event_loop(monkeypatch, tmp_path):
    from app.core.parsing import local_ocr

    path = tmp_path / "page.png"
    Image.new("RGB", (80, 40), "white").save(path)
    original_open = Image.open

    def slow_open(file_path):
        time.sleep(0.2)
        return original_open(file_path)

    monkeypatch.setattr(Image, "open", slow_open)
    monkeypatch.setattr(local_ocr, "recognize", lambda image: local_ocr.OCRPage([
        local_ocr.OCRLine("文字", (2, 2, 60, 20), 0.99)
    ], 0.01))
    task = asyncio.create_task(DocumentParser().parse(str(path), "png"))
    started = time.perf_counter()
    await asyncio.sleep(0.04)
    elapsed = time.perf_counter() - started
    await task
    assert elapsed < 0.15


@pytest.mark.asyncio
async def test_queued_image_waits_before_decode_and_reports_queue_time(monkeypatch, tmp_path):
    from app.core.parsing import local_ocr

    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (80, 40), "white").save(first)
    Image.new("RGB", (120, 50), "white").save(second)

    loop = asyncio.get_running_loop()
    first_ocr_started = asyncio.Event()
    second_parse_started = asyncio.Event()
    second_decode_started = threading.Event()
    release_first_ocr = threading.Event()
    original_open = Image.open
    original_parse = DocumentParser.parse

    def tracked_open(file_path, *args, **kwargs):
        if str(file_path) == str(second):
            second_decode_started.set()
        return original_open(file_path, *args, **kwargs)

    def held_ocr(image):
        if image.size == (80, 40):
            loop.call_soon_threadsafe(first_ocr_started.set)
            if not release_first_ocr.wait(3):
                raise TimeoutError("test OCR gate was not released")
        return local_ocr.OCRPage([local_ocr.OCRLine("文字", (2, 2, 60, 20), 0.99)], 0.01)

    async def observed_parse(self, file_path, file_type):
        if file_path == str(second):
            second_parse_started.set()
        return await original_parse(self, file_path, file_type)

    monkeypatch.setattr(Image, "open", tracked_open)
    monkeypatch.setattr(local_ocr, "recognize", held_ocr)
    monkeypatch.setattr(DocumentParser, "parse", observed_parse)

    parser = DocumentParser()
    first_task = asyncio.create_task(parser.parse(str(first), "png"))
    second_task = None
    try:
        await asyncio.wait_for(first_ocr_started.wait(), 1)
        second_task = asyncio.create_task(parser.parse(str(second), "png"))
        await asyncio.wait_for(second_parse_started.wait(), 1)
        # observed_parse reaches the parser's first await before this task resumes.
        assert not await asyncio.to_thread(second_decode_started.wait, 0.1)
        await asyncio.wait_for(asyncio.sleep(0), 0.2)
    finally:
        release_first_ocr.set()
        results = await asyncio.gather(first_task, second_task) if second_task else [await first_task]

    assert results[1].metadata["ocr_queue_seconds"] > 0.05


@pytest.mark.asyncio
async def test_queued_scan_pdf_waits_before_rendering_after_image_ocr(monkeypatch, tmp_path):
    import fitz
    from app.core.parsing import local_ocr, settings

    first = tmp_path / "first.png"
    Image.new("RGB", (80, 40), "white").save(first)
    scanned_image = tmp_path / "scan.png"
    Image.new("RGB", (400, 600), "white").save(scanned_image)
    pdf = tmp_path / "scan.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    page.insert_image(page.rect, filename=str(scanned_image))
    doc.save(pdf)
    doc.close()

    loop = asyncio.get_running_loop()
    first_ocr_started = asyncio.Event()
    pdf_parse_started = asyncio.Event()
    second_render_started = threading.Event()
    release_first_ocr = threading.Event()
    original_get_pixmap = fitz.Page.get_pixmap
    original_parse = DocumentParser.parse

    def tracked_get_pixmap(self, *args, **kwargs):
        second_render_started.set()
        return original_get_pixmap(self, *args, **kwargs)

    def held_ocr(image):
        if image.size == (80, 40):
            loop.call_soon_threadsafe(first_ocr_started.set)
            if not release_first_ocr.wait(3):
                raise TimeoutError("test OCR gate was not released")
        return local_ocr.OCRPage([local_ocr.OCRLine("文字", (2, 2, 60, 20), 0.99)], 0.01)

    async def observed_parse(self, file_path, file_type):
        if file_path == str(pdf):
            pdf_parse_started.set()
        return await original_parse(self, file_path, file_type)

    monkeypatch.setattr(settings, "use_docling", False)
    monkeypatch.setattr(fitz.Page, "get_pixmap", tracked_get_pixmap)
    monkeypatch.setattr(local_ocr, "recognize", held_ocr)
    monkeypatch.setattr(DocumentParser, "parse", observed_parse)

    parser = DocumentParser()
    first_task = asyncio.create_task(parser.parse(str(first), "png"))
    second_task = None
    try:
        await asyncio.wait_for(first_ocr_started.wait(), 1)
        second_task = asyncio.create_task(parser.parse(str(pdf), "pdf"))
        await asyncio.wait_for(pdf_parse_started.wait(), 1)
        assert not await asyncio.to_thread(second_render_started.wait, 0.1)
        await asyncio.wait_for(asyncio.sleep(0), 0.2)
    finally:
        release_first_ocr.set()
        results = await asyncio.gather(first_task, second_task) if second_task else [await first_task]

    assert results[1].metadata["ocr_queue_seconds"] >= 0


@pytest.mark.asyncio
async def test_queued_ocr_does_not_exhaust_default_thread_pool(monkeypatch, tmp_path):
    from app.core.parsing import local_ocr

    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    Image.new("RGB", (80, 40), "white").save(first)
    Image.new("RGB", (120, 50), "white").save(second)

    loop = asyncio.get_running_loop()
    first_ocr_started = asyncio.Event()
    second_parse_started = asyncio.Event()
    release_workers = threading.Event()
    original_open = Image.open
    original_parse = DocumentParser.parse

    def blocking_second_open(file_path, *args, **kwargs):
        if str(file_path) == str(second) and not release_workers.wait(3):
            raise TimeoutError("test decode gate was not released")
        return original_open(file_path, *args, **kwargs)

    def held_ocr(image):
        if image.size == (80, 40):
            loop.call_soon_threadsafe(first_ocr_started.set)
            if not release_workers.wait(3):
                raise TimeoutError("test OCR gate was not released")
        return local_ocr.OCRPage([local_ocr.OCRLine("文字", (2, 2, 60, 20), 0.99)], 0.01)

    async def observed_parse(self, file_path, file_type):
        if file_path == str(second):
            second_parse_started.set()
        return await original_parse(self, file_path, file_type)

    monkeypatch.setattr(Image, "open", blocking_second_open)
    monkeypatch.setattr(local_ocr, "recognize", held_ocr)
    monkeypatch.setattr(DocumentParser, "parse", observed_parse)

    # Two workers make the capacity failure reproducible without spawning 32 tasks.
    prior_executor = loop._default_executor
    bounded_executor = ThreadPoolExecutor(max_workers=2)
    loop.set_default_executor(bounded_executor)
    parser = DocumentParser()
    first_task = asyncio.create_task(parser.parse(str(first), "png"))
    second_task = None
    try:
        await asyncio.wait_for(first_ocr_started.wait(), 1)
        second_task = asyncio.create_task(parser.parse(str(second), "png"))
        await asyncio.wait_for(second_parse_started.wait(), 1)
        assert await asyncio.wait_for(asyncio.to_thread(lambda: "available"), 0.2) == "available"
    finally:
        release_workers.set()
        try:
            await asyncio.gather(first_task, second_task) if second_task else await first_task
        finally:
            loop._default_executor = prior_executor
            bounded_executor.shutdown(wait=True)


@pytest.mark.asyncio
async def test_image_exposes_formula_candidate_without_loading_formula_model(monkeypatch, tmp_path):
    from app.core.parsing import local_ocr
    from app.core.parsing import formula_local

    path = tmp_path / "math.png"
    Image.new("RGB", (200, 80), "white").save(path)
    line = local_ocr.OCRLine("x^2 = 1", (20, 20, 100, 40), 0.99)
    monkeypatch.setattr(local_ocr, "recognize", lambda image: local_ocr.OCRPage([line], 0.1))
    monkeypatch.setattr(formula_local, "_pipeline", lambda model_dir: pytest.fail("heavy model loaded"))

    parsed = await DocumentParser().parse(str(path), "png")
    assert parsed.text == "x^2 = 1"
    assert parsed.metadata["formula_candidates"] == [
        {"ocr_text": "x^2 = 1", "box": [14, 14, 106, 46]}
    ]


@pytest.mark.asyncio
async def test_table_keeps_ocr_line_that_crosses_cells(monkeypatch, tmp_path):
    from app.core.parsing import local_ocr

    image = Image.new("RGB", (240, 120), "white")
    draw = ImageDraw.Draw(image)
    for x in (10, 110, 230):
        draw.line((x, 10, x, 110), fill="black", width=2)
    for y in (10, 60, 110):
        draw.line((10, y, 230, y), fill="black", width=2)
    path = tmp_path / "table.png"
    image.save(path)
    lines = [
        local_ocr.OCRLine("课程学分", (80, 20, 160, 40), 0.99),
        local_ocr.OCRLine("数学", (20, 70, 80, 90), 0.99),
        local_ocr.OCRLine("4", (130, 70, 140, 90), 0.99),
    ]
    monkeypatch.setattr(local_ocr, "recognize", lambda image: local_ocr.OCRPage(lines, 0.1))
    parsed = await DocumentParser().parse(str(path), "png")
    assert "课程学分" in parsed.text
    assert "| 数学 | 4 |" in parsed.text
