"""Exercise a page that mixes searchable prose with a scanned text panel."""

import asyncio
from io import BytesIO
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

import fitz
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.config import settings  # noqa: E402
from app.core.parsing import DocumentParser  # noqa: E402


async def main():
    output = ROOT / "docs" / "ppstructure-subset-evidence"
    output.mkdir(parents=True, exist_ok=True)
    pdf = output / "partial-scan-page.pdf"

    image = Image.new("RGB", (400, 300), "white")
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    font = ImageFont.truetype(str(font_path), 29) if font_path.exists() else ImageFont.load_default()
    drawing = ImageDraw.Draw(image)
    drawing.text((20, 65), "SCAN SECTION", fill="black", font=font)
    drawing.text((20, 125), "UNIQUE CONTENT", fill="black", font=font)
    image_bytes = BytesIO()
    image.save(image_bytes, format="PNG")

    with fitz.open() as document:
        page = document.new_page(width=400, height=600)
        page.insert_textbox(fitz.Rect(20, 30, 380, 270), "DIGITAL PROSE " * 25)
        page.insert_image(fitz.Rect(0, 300, 400, 600), stream=image_bytes.getvalue())
        document.save(pdf)

    prior = settings.use_docling
    settings.use_docling = False
    try:
        started = perf_counter()
        parsed = await DocumentParser().parse(str(pdf), "pdf")
        elapsed = perf_counter() - started
    finally:
        settings.use_docling = prior

    payload = {
        "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "seconds": round(elapsed, 3),
        "parser": parsed.metadata["parser"],
        "page_methods": parsed.metadata["page_methods"],
        "partial_scan_pages": parsed.metadata["partial_scan_pages"],
        "digital_preserved": "DIGITAL PROSE" in parsed.text,
        "scan_preserved": "SCAN SECTION" in parsed.text and "UNIQUE CONTENT" in parsed.text,
        "text": parsed.text,
    }
    (output / "partial-scan-probe.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in payload.items() if key != "text"}, indent=2))
    if not payload["digital_preserved"] or not payload["scan_preserved"]:
        raise RuntimeError("real OCR failed to preserve one of the two text sources")


if __name__ == "__main__":
    asyncio.run(main())
