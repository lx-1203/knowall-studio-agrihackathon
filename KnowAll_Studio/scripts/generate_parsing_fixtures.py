"""Generate reproducible local parsing fixtures with checked cell geometry."""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


TABLE_ROWS = [
    ["课程", "学分", "成绩", "学期"],
    ["高等数学", "4", "85", "2023-1"],
    ["无机及分析化学", "5", "72", "2023-1"],
    ["大学物理B", "3", "91", "2023-2"],
    ["Python程序设计", "3", "88", "2023-2"],
    ["动物学", "2", "76", "2024-1"],
]

FONT_CANDIDATES = (
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simsun.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
)


def _font(size: int):
    for path in FONT_CANDIDATES:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def generate_table(output_dir: Path) -> dict:
    """Draw a ruled 6×4 table and reject labels that cross cell borders."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    xs = [40, 420, 600, 780, 1260]
    ys = [40 + 105 * row for row in range(7)]
    image = Image.new("RGB", (1300, 710), "white")
    draw = ImageDraw.Draw(image)
    font = _font(32)
    cells = []

    for row, values in enumerate(TABLE_ROWS):
        for column, value in enumerate(values):
            bounds = [xs[column], ys[row], xs[column + 1], ys[row + 1]]
            text_x, text_y = bounds[0] + 18, bounds[1] + 26
            text_bounds = list(draw.textbbox((text_x, text_y), value, font=font))
            if not (
                bounds[0] + 12 <= text_bounds[0] < text_bounds[2] <= bounds[2] - 12
                and bounds[1] + 8 <= text_bounds[1] < text_bounds[3] <= bounds[3] - 8
            ):
                raise ValueError(f"Text crosses cell border: row={row} col={column} text={value!r}")
            draw.text((text_x, text_y), value, fill="black", font=font)
            cells.append({
                "row": row,
                "column": column,
                "text": value,
                "bounds": bounds,
                "text_bounds": text_bounds,
            })

    for x in xs:
        draw.line([(x, ys[0]), (x, ys[-1])], fill="black", width=4)
    for y in ys:
        draw.line([(xs[0], y), (xs[-1], y)], fill="black", width=4)

    image_path = output_dir / "T_table_valid.png"
    image.save(image_path)
    manifest = {
        "image": str(image_path.resolve()),
        "rows": len(TABLE_ROWS),
        "columns": len(TABLE_ROWS[0]),
        "cells": cells,
    }
    (output_dir / "T_table_valid.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(generate_table(arguments.output_dir), ensure_ascii=False))
