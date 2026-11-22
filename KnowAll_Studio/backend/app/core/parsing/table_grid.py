"""Recover simple ruled tables from page pixels and OCR coordinates."""

from dataclasses import dataclass

from .local_ocr import OCRLine, OCRPage

MAX_CELL_RETRIES = 64


@dataclass(frozen=True)
class RecoveredTable:
    cells: list[list[str]]
    bounds: tuple[int, int, int, int]
    markdown: str
    unassigned_lines: list[OCRLine]
    retry_attempts: int = 0
    retry_truncated: bool = False


def _runs(indices):
    groups = []
    for index in indices:
        if not groups or index > groups[-1][-1] + 1:
            groups.append([index])
        else:
            groups[-1].append(index)
    return [round(sum(group) / len(group)) for group in groups]


def _grid_lines(image):
    import numpy as np

    gray = np.asarray(image.convert("L"))
    dark = gray < 105
    height, width = dark.shape

    def long_segments(rows, minimum):
        indices = []
        for index, row in enumerate(rows):
            positions = np.flatnonzero(row)
            if positions.size < minimum:
                continue
            breaks = np.flatnonzero(np.diff(positions) > 1)
            lengths = np.diff(np.r_[-1, breaks, positions.size - 1])
            if lengths.max() >= minimum:
                indices.append(index)
        return _runs(indices)

    # A table can occupy only a corner of the page. Detect continuous rules,
    # then check their intersections instead of measuring ink across the page.
    horizontal = long_segments(dark, max(30, round(width * 0.1)))
    vertical = long_segments(dark.T, max(30, round(height * 0.1)))
    if len(horizontal) < 3 or len(vertical) < 3:
        return None
    # Reject isolated underlines or a text column: most intersections must exist.
    hits = sum(bool(dark[max(0, y - 2):y + 3, max(0, x - 2):x + 3].any())
               for y in horizontal for x in vertical)
    if hits < len(horizontal) * len(vertical) * 0.9:
        return None
    # Diagram connectors may cross at points without forming complete cell edges.
    if any(dark[max(0, y - 2):y + 3, vertical[0]:vertical[-1] + 1].any(axis=0).mean() < 0.97
           for y in horizontal):
        return None
    if any(dark[horizontal[0]:horizontal[-1] + 1, max(0, x - 2):x + 3].any(axis=1).mean() < 0.97
           for x in vertical):
        return None
    return horizontal, vertical


def _markdown(cells):
    esc = lambda value: value.replace("|", "\\|").replace("\n", "<br>")
    rows = ["| " + " | ".join(esc(cell) for cell in row) + " |" for row in cells]
    rows.insert(1, "| " + " | ".join("---" for _ in cells[0]) + " |")
    return "\n".join(rows)


def recover_table(image, page: OCRPage, *, retry_empty: bool = False) -> RecoveredTable | None:
    import numpy as np

    grid = _grid_lines(image)
    if grid is None:
        return None
    ys, xs = grid
    cells = [["" for _ in range(len(xs) - 1)] for _ in range(len(ys) - 1)]
    assigned = set()
    for index, line in enumerate(page.lines):
        left, top, right, bottom = line.box
        cx, cy = (left + right) / 2, (top + bottom) / 2
        col = int(np.searchsorted(xs, cx) - 1)
        row = int(np.searchsorted(ys, cy) - 1)
        if 0 <= row < len(cells) and 0 <= col < len(cells[0]):
            overlap = max(0, min(right, xs[col + 1]) - max(left, xs[col])) * max(
                0, min(bottom, ys[row + 1]) - max(top, ys[row])
            )
            box_area = max(0, right - left) * max(0, bottom - top)
            if box_area and overlap / box_area >= 0.8:
                cells[row][col] = (cells[row][col] + " " + line.text).strip()
                assigned.add(index)

    retry_attempts = 0
    retry_truncated = False
    if retry_empty:
        from .local_ocr import recognize_crop

        gray = np.asarray(image.convert("L"))
        for row, values in enumerate(cells):
            for col, value in enumerate(values):
                if value:
                    continue
                x1, x2 = xs[col], xs[col + 1]
                y1, y2 = ys[row], ys[row + 1]
                inner = gray[y1 + 6:y2 - 6, x1 + 6:x2 - 6]
                if inner.size == 0:
                    continue
                ink_y, ink_x = np.nonzero(inner < 160)
                if len(ink_x) < 48:
                    continue
                ink_width = int(ink_x.max() - ink_x.min() + 1)
                ink_height = int(ink_y.max() - ink_y.min() + 1)
                if min(ink_width, ink_height) < 8 or len(ink_x) / (ink_width * ink_height) > 0.75:
                    continue
                if retry_attempts >= MAX_CELL_RETRIES:
                    retry_truncated = True
                    break
                retry_attempts += 1
                candidate, score = recognize_crop(image.crop((x1, y1, x2, y2)))
                if candidate.strip() and score >= 0.8:
                    cells[row][col] = candidate.strip()
            if retry_truncated:
                break

    if not any(value for row in cells for value in row):
        return None
    unassigned = [line for index, line in enumerate(page.lines) if index not in assigned]
    return RecoveredTable(cells, (xs[0], ys[0], xs[-1], ys[-1]),
                          _markdown(cells), unassigned, retry_attempts, retry_truncated)
