"""Verify the real Docling-plus-RapidOCR path on a tiny mixed PDF."""

import asyncio
import json
from pathlib import Path
import sys
from time import perf_counter

import fitz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.parsing import DocumentParser  # noqa: E402


async def main():
    output = ROOT / "docs" / "ppstructure-subset-evidence"
    output.mkdir(parents=True, exist_ok=True)
    pdf = output / "mixed-digital-scan.pdf"
    scan = output / "valid-table" / "T_table_valid.png"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 90), "Chapter 1 Digital Text", fontsize=18)
    page.insert_text((72, 130), "A searchable paragraph on the first page.", fontsize=12)
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=str(scan))
    doc.save(pdf)
    doc.close()
    started = perf_counter()
    parsed = await DocumentParser().parse(str(pdf), "pdf")
    payload = {
        "pdf": str(pdf),
        "seconds": round(perf_counter() - started, 3),
        "page_count": parsed.page_count,
        "parser": parsed.metadata.get("parser"),
        "page_methods": parsed.metadata.get("page_methods"),
        "page_items": parsed.page_items,
        "headings": [str(item) for item in parsed.headings],
        "table_count": len(parsed.tables),
        "table_pages": [table.page for table in parsed.tables],
        "first_page_markdown": parsed.page_texts[0] if parsed.page_texts else None,
        "text": parsed.text,
    }
    (output / "mixed-pdf-probe.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key not in ("text", "page_items", "headings")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
