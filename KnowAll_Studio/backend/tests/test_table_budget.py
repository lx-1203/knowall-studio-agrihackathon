"""Dense ruled pages should not trigger unbounded cell OCR retries."""

from PIL import Image, ImageDraw

from app.core.parsing import local_ocr
from app.core.parsing.table_grid import recover_table


def test_dense_grid_limits_retry_calls_and_keeps_existing_ocr(monkeypatch):
    image = Image.new("RGB", (500, 500), "white")
    drawing = ImageDraw.Draw(image)
    for point in range(10, 491, 30):
        drawing.line((10, point, 490, point), fill="black", width=2)
        drawing.line((point, 10, point, 490), fill="black", width=2)
    for top in range(10, 490, 30):
        for left in range(10, 490, 30):
            drawing.text((left + 7, top + 8), "ABCD", fill="black")

    original = local_ocr.OCRLine("Original", (20, 20, 35, 29), 0.99)
    outside = local_ocr.OCRLine("Page intro", (15, 1, 100, 8), 0.99)
    calls = []

    def recognize_crop(crop):
        calls.append(crop.size)
        return "Retried", 0.99

    monkeypatch.setattr(local_ocr, "recognize_crop", recognize_crop)
    table = recover_table(image, local_ocr.OCRPage([original, outside], 0.01), retry_empty=True)

    assert table is not None
    assert len(table.cells) == 16
    assert len(calls) <= 64
    assert any("Original" in cell for row in table.cells for cell in row)
    assert any(line.text == "Page intro" for line in table.unassigned_lines)
    assert any(not cell for row in table.cells for cell in row)
