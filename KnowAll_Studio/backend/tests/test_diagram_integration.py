"""Visible diagram connectors survive through the product parser."""

import pytest
from PIL import Image, ImageDraw

from app.core.parsing import DocumentParser


@pytest.mark.asyncio
async def test_image_exposes_connected_cards_without_changing_ocr_text(monkeypatch, tmp_path):
    from app.core.parsing import local_ocr

    image = Image.new("RGB", (380, 150), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 35, 140, 105), outline="black", width=3)
    draw.rectangle((240, 35, 350, 105), outline="black", width=3)
    draw.line((140, 70, 240, 70), fill="black", width=3)
    path = tmp_path / "diagram.png"
    image.save(path)

    lines = [
        local_ocr.OCRLine("原因", (59, 61, 96, 79), 0.99),
        local_ocr.OCRLine("结果", (278, 61, 314, 79), 0.99),
    ]
    monkeypatch.setattr(local_ocr, "recognize", lambda _image: local_ocr.OCRPage(lines, 0.1))

    parsed = await DocumentParser().parse(str(path), "png")
    assert parsed.text == "原因\n结果"
    assert [node["text"] for node in parsed.metadata["diagram_nodes"]] == ["原因", "结果"]
    assert parsed.metadata["diagram_edges"] == [
        {"source": "p1_diagram_node_1", "target": "p1_diagram_node_2",
         "relation": "connected", "confidence": 1.0,
         "evidence": "solid_straight_connector", "page": 1}
    ]
