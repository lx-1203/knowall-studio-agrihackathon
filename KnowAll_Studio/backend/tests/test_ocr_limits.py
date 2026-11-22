"""OCR inputs have pixel budgets before allocating decoded page buffers."""

import math

import fitz
import pytest
from PIL import Image

from app.core.parsing import DocumentParser, _pdf_ocr_scale


def test_pdf_ocr_scale_keeps_a4_at_200_dpi_and_caps_large_page():
    normal = _pdf_ocr_scale(fitz.Rect(0, 0, 595, 842))
    assert math.isclose(normal, 200 / 72)

    huge = _pdf_ocr_scale(fitz.Rect(0, 0, 4000, 4000))
    assert huge < normal
    assert (4000 * huge) ** 2 <= 6_000_000 + 1
    assert 4000 * huge <= 4096


@pytest.mark.asyncio
async def test_oversize_image_rejected_before_rgb_conversion(monkeypatch):
    class HugeImage:
        size = (100_000, 100_000)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def convert(self, *_args):
            pytest.fail("oversize image was decoded")

    monkeypatch.setattr(Image, "open", lambda _path: HugeImage())
    with pytest.raises(ValueError, match="image exceeds OCR pixel limit"):
        await DocumentParser().parse("large.png", "png")
