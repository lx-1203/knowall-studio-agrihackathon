"""Exercise the product parser against local OCR fixtures and save evidence."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.parsing import DocumentParser  # noqa: E402
from ppstructure_subset_benchmark import character_recall, line_recovery  # noqa: E402


async def benchmark(fixture_dir: Path, output: Path, table_image: Path):
    parser = DocumentParser()
    results = []
    for name in ("D_diagram", "P_prose1", "P_prose2", "P_prose3"):
        path = fixture_dir / f"{name}.png"
        expected = (fixture_dir / f"{name}.gt.txt").read_text(encoding="utf-8")
        started = perf_counter()
        parsed = await parser.parse(str(path), "png")
        results.append({
            "case": name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "seconds": round(perf_counter() - started, 3),
            "ocr_seconds": round(parsed.metadata["ocr_seconds"], 3),
            "ocr_queue_seconds": round(parsed.metadata["ocr_queue_seconds"], 3),
            "diagram_node_count": len(parsed.metadata["diagram_nodes"]),
            "diagram_edge_count": len(parsed.metadata["diagram_edges"]),
            "character_recall": round(character_recall(expected, parsed.text), 2),
            "line_recovery": round(line_recovery(expected, parsed.text), 2),
            "page_count": parsed.page_count,
            "parser": parsed.metadata["parser"],
            "text": parsed.text,
        })
    pdf_path = fixture_dir / "P_prose1.pdf"
    if pdf_path.exists():
        expected = (fixture_dir / "P_prose1.gt.txt").read_text(encoding="utf-8")
        started = perf_counter()
        parsed = await parser.parse(str(pdf_path), "pdf")
        results.append({
            "case": "P_prose1_pdf",
            "sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            "seconds": round(perf_counter() - started, 3),
            "ocr_seconds": round(parsed.metadata["ocr_seconds"], 3),
            "ocr_queue_seconds": round(parsed.metadata["ocr_queue_seconds"], 3),
            "diagram_node_count": len(parsed.metadata["diagram_nodes"]),
            "diagram_edge_count": len(parsed.metadata["diagram_edges"]),
            "character_recall": round(character_recall(expected, parsed.text), 2),
            "line_recovery": round(line_recovery(expected, parsed.text), 2),
            "page_count": parsed.page_count,
            "parser": parsed.metadata["parser"],
            "text": parsed.text,
        })
    manifest = json.loads(table_image.with_suffix(".json").read_text(encoding="utf-8"))
    started = perf_counter()
    parsed = await parser.parse(str(table_image), "png")
    actual = parsed.metadata["table_cells"]
    expected = [["" for _ in range(manifest["columns"])] for _ in range(manifest["rows"])]
    for cell in manifest["cells"]:
        expected[cell["row"]][cell["column"]] = cell["text"]
    results.append({
        "case": "T_table_valid",
        "sha256": hashlib.sha256(table_image.read_bytes()).hexdigest(),
        "seconds": round(perf_counter() - started, 3),
        "ocr_seconds": round(parsed.metadata["ocr_seconds"], 3),
        "ocr_queue_seconds": round(parsed.metadata["ocr_queue_seconds"], 3),
        "correct_cells": sum(a == b for row_a, row_b in zip(actual or [], expected)
                             for a, b in zip(row_a, row_b)),
        "total_cells": 24,
        "cells": actual,
        "text": parsed.text,
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps([{key: value for key, value in item.items() if key not in ("text", "cells")}
                      for item in results], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--fixture-dir", type=Path, required=True)
    arguments.add_argument("--output", type=Path, required=True)
    arguments.add_argument("--table-image", type=Path, required=True)
    args = arguments.parse_args()
    asyncio.run(benchmark(args.fixture_dir, args.output, args.table_image))
