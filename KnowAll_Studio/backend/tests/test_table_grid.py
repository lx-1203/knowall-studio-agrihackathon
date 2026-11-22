"""Regression tests for ruled-table geometry and OCR placement."""

from PIL import Image, ImageDraw

from app.core.parsing.local_ocr import OCRLine, OCRPage
from app.core.parsing.table_grid import recover_table


def _grid(canvas=(800, 600), bounds=(430, 300, 750, 500)):
    image = Image.new("RGB", canvas, "white")
    draw = ImageDraw.Draw(image)
    x1, y1, x2, y2 = bounds
    for x in (x1, (x1 + x2) // 2, x2):
        draw.line((x, y1, x, y2), fill="black", width=2)
    for y in (y1, (y1 + y2) // 2, y2):
        draw.line((x1, y, x2, y), fill="black", width=2)
    return image


def test_localized_grid_recovers_cells_without_page_wide_lines():
    image = _grid()
    lines = [OCRLine("课程", (450, 320, 500, 350), 0.99),
             OCRLine("学分", (610, 320, 660, 350), 0.99)]

    result = recover_table(image, OCRPage(lines, 0))

    assert result is not None
    assert result.bounds == (430, 300, 750, 500)
    assert result.cells == [["课程", "学分"], ["", ""]]


def test_grid_touching_page_edges_is_recovered():
    image = _grid(canvas=(320, 200), bounds=(0, 0, 240, 160))

    result = recover_table(image, OCRPage([OCRLine("课程", (20, 20, 70, 40), 0.99)], 0))

    assert result is not None
    assert result.bounds == (0, 0, 240, 160)
    assert len(result.cells) == 2
    assert len(result.cells[0]) == 2


def test_empty_grid_without_text_is_not_a_table():
    assert recover_table(_grid(), OCRPage([], 0)) is None


def test_cross_cell_ocr_box_is_not_assigned_to_one_cell():
    image = _grid()
    lines = [OCRLine("跨列误识别", (540, 325, 640, 355), 0.99),
             OCRLine("课程", (450, 325, 500, 355), 0.99)]

    result = recover_table(image, OCRPage(lines, 0))

    assert result is not None
    assert result.cells[0] == ["课程", ""]


def test_disconnected_diagram_boxes_are_not_a_table():
    image = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(image)
    for y in (300, 410):
        for x in (430, 600):
            draw.rectangle((x, y, x + 140, y + 90), outline="black", width=2)

    lines = [OCRLine("诗词", (450, 320, 500, 350), 0.99)]
    assert recover_table(image, OCRPage(lines, 0)) is None


def test_empty_cell_noise_does_not_trigger_recognition(monkeypatch):
    from app.core.parsing import local_ocr

    image = _grid()
    ImageDraw.Draw(image).rectangle((625, 425, 630, 430), fill="black")
    calls = []

    def fake_recognize(_image):
        calls.append(_image.size)
        return "幻觉", 0.99

    monkeypatch.setattr(local_ocr, "recognize_crop", fake_recognize)
    result = recover_table(image, OCRPage([OCRLine("课程", (450, 325, 500, 355), 0.99)], 0), retry_empty=True)

    assert result is not None
    assert result.cells == [["课程", ""], ["", ""]]
    assert calls == []
