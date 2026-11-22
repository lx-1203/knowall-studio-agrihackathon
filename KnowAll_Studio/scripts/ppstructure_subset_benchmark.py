"""Benchmark PP-StructureV3 layout detection + OCR on the parsing fixtures.

Example:
    python scripts/ppstructure_subset_benchmark.py --fixture-dir C:/path/to/fx

The table and formula fixtures are diagnostic only: this configuration disables
their structure/semantic recognition models by design.
"""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback


PUNCTUATION = str.maketrans(
    "，。．、：；！？（）［］“”‘’—－～《》",
    ",..,:;!?()[]\"\"''--~<>",
)


def canonical(text: str) -> str:
    return re.sub(r"\s", "", text).replace("——", "-").translate(PUNCTUATION)


def character_recall(expected: str, actual: str) -> float:
    reference = Counter(canonical(expected))
    found = Counter(canonical(actual))
    return 100 * sum(min(count, found[char]) for char, count in reference.items()) / max(1, sum(reference.values()))


def line_recovery(expected: str, actual: str) -> float:
    found = canonical(actual)
    lines = [canonical(line) for line in expected.splitlines() if len(canonical(line)) >= 6]
    return 100 * sum(line in found for line in lines) / max(1, len(lines))


CASE_NAMES = ("D_diagram", "P_prose1", "P_prose2", "P_prose3", "T_table", "F_formula")


def fixtures(directory: Path, selected=None):
    table = "\n".join([
        "课程 学分 成绩 学期", "高等数学 4 85 2023-1",
        "无机及分析化学 5 72 2023-1", "大学物理B 3 91 2023-2",
        "Python程序设计 3 88 2023-2", "动物学 2 76 2024-1",
    ])
    formula = "\n".join([
        "x = (-b +/- sqrt(b^2 - 4ac)) / (2a)", "e^(i*pi) + 1 = 0",
        "Integral_0^Inf e^(-x^2) dx = sqrt(pi)/2", "A v = lambda v",
        "f(x) = Sum_n f^(n)(a)/n! (x-a)^n",
        "p(x) = 1/(sigma sqrt(2pi)) e^(-(x-mu)^2/(2sigma^2))",
    ])
    for name in CASE_NAMES:
        if selected and name not in selected:
            continue
        image = directory / f"{name}.png"
        expected = (
            table if name == "T_table" else formula if name == "F_formula"
            else (directory / f"{name}.gt.txt").read_text(encoding="utf-8")
        )
        yield name, image, expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case", action="append", choices=CASE_NAMES, help="Run only a named fixture; repeat for multiple cases")
    parser.add_argument("--scale", type=float, default=1.0)
    args = parser.parse_args()
    if not 0 < args.scale <= 1:
        parser.error("--scale must be in (0, 1]")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(Path.home() / ".paddlex"))
    os.environ["FLAGS_use_mkldnn"] = "0"
    os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "False"
    os.environ["FLAGS_enable_pir_api"] = "0"
    sys.stdout.reconfigure(encoding="utf-8")

    # This installed Windows environment fails to load torch\lib\shm.dll if
    # Paddle loads first. Importing torch first avoids the DLL order conflict.
    import torch  # noqa: F401
    import numpy as np
    from PIL import Image
    from paddleocr import PPStructureV3

    started = time.perf_counter()
    pipeline = PPStructureV3(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        use_seal_recognition=False,
        use_table_recognition=False,
        use_formula_recognition=False,
        use_chart_recognition=False,
        use_region_detection=False,
    )
    init_seconds = time.perf_counter() - started
    print(f"init_seconds={init_seconds:.1f}", flush=True)

    rows = []
    for name, image_path, expected in fixtures(args.fixture_dir, args.case):
        try:
            image = Image.open(image_path).convert("RGB")
            if args.scale != 1:
                image = image.resize(
                    (round(image.width * args.scale), round(image.height * args.scale)),
                    Image.Resampling.LANCZOS,
                )
            print(f"start={name} size={image.width}x{image.height}", flush=True)
            started = time.perf_counter()
            result = pipeline.predict(np.asarray(image))[0]
            seconds = time.perf_counter() - started
            blocks = result["parsing_res_list"]
            layout_text = "\n".join(str(block.content) for block in blocks if block.content)
            raw_text = "\n".join(result["overall_ocr_res"]["rec_texts"])
            text = layout_text or raw_text
            (args.output_dir / f"{name}.txt").write_text(text, encoding="utf-8")
            (args.output_dir / f"{name}.raw.txt").write_text(raw_text, encoding="utf-8")
            row = {
                "case": name,
                "size": [image.width, image.height],
                "seconds": round(seconds, 2),
                "character_recall": round(character_recall(expected, text), 2),
                "line_recovery": round(line_recovery(expected, text), 2),
                "raw_character_recall": round(character_recall(expected, raw_text), 2),
                "raw_line_recovery": round(line_recovery(expected, raw_text), 2),
                "characters": len(text),
                "ocr_lines": len(result["overall_ocr_res"]["rec_texts"]),
                "layout_blocks": len(blocks),
                "block_labels": [block.label for block in blocks],
                "model_settings": result["model_settings"],
            }
            print(json.dumps(row, ensure_ascii=False), flush=True)
        except Exception as error:
            row = {"case": name, "error": f"{type(error).__name__}: {error}"}
            traceback.print_exc()
            print(json.dumps(row, ensure_ascii=False), flush=True)
        rows.append(row)
        (args.output_dir / "scores.json").write_text(
            json.dumps({"init_seconds": round(init_seconds, 2), "scale": args.scale, "cases": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 1 if any("error" in row for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
