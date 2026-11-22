"""Conservative local extraction of framed diagram nodes and visible connectors.

The output is deliberately an undirected ``connected`` relation. A solid line
does not establish arrow direction or a parent-child hierarchy. Unframed
mind maps, curved paths, and ambiguous connectors are left unlinked.
"""

from dataclasses import dataclass, field

import numpy as np

from .local_ocr import OCRPage


Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class DiagramNode:
    id: str
    text: str
    box: Box


@dataclass(frozen=True)
class DiagramEdge:
    source: str
    target: str
    relation: str
    confidence: float
    evidence: str = "solid_straight_connector"


@dataclass
class DiagramRelations:
    nodes: list[DiagramNode] = field(default_factory=list)
    edges: list[DiagramEdge] = field(default_factory=list)


def _ink_mask(image) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB") if hasattr(image, "convert") else image)
    if rgb.ndim == 2:
        return rgb < 180
    return np.min(rgb[:, :, :3], axis=2) < 180


def _horizontal_runs(ink: np.ndarray) -> list[tuple[int, int, int]]:
    """Record one representative row for each long horizontal stroke."""
    minimum = 45
    runs: list[tuple[int, int, int]] = []
    previous: list[tuple[int, int]] = []
    for y, row in enumerate(ink):
        changes = np.diff(np.r_[False, row, False].astype(np.int8))
        current = [
            (int(start), int(end) - 1)
            for start, end in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1))
            if end - start >= minimum
        ]
        for left, right in current:
            if not any(abs(left - old_left) <= 4 and abs(right - old_right) <= 4 for old_left, old_right in previous):
                runs.append((y, left, right))
        previous = current
    return runs


def _vertical_side(ink: np.ndarray, candidate_x: int, y1: int, y2: int, direction: int) -> int | None:
    """Find the actual side, allowing rounded corners beside a top stroke."""
    height, width = ink.shape
    start = max(0, y1 + 7)
    stop = min(height, y2 - 6)
    if stop - start < 18:
        return None
    offsets = range(-14, 4) if direction < 0 else range(-3, 15)
    options = [candidate_x + offset for offset in offsets if 0 <= candidate_x + offset < width]
    if not options:
        return None
    best = max(options, key=lambda x: float(ink[start:stop, x].mean()))
    return best if ink[start:stop, best].mean() >= 0.82 else None


def _framed_boxes(ink: np.ndarray) -> list[Box]:
    height, width = ink.shape
    runs = _horizontal_runs(ink)
    boxes: set[Box] = set()
    for index, (top, left, right) in enumerate(runs):
        for bottom, lower_left, lower_right in runs[index + 1:]:
            separation = bottom - top
            if separation < 30:
                continue
            if separation > min(260, height // 2):
                break
            if abs(left - lower_left) > 5 or abs(right - lower_right) > 5:
                continue
            x1 = _vertical_side(ink, left, top, bottom, -1)
            x2 = _vertical_side(ink, right, top, bottom, 1)
            if x1 is None or x2 is None or x2 - x1 < 45 or x2 - x1 > width * 0.9:
                continue
            # A continuous divider is strong evidence for a table, not one card.
            inner = ink[top + 7:bottom - 6, x1 + 8:x2 - 7]
            if inner.size and np.any(inner.mean(axis=0) >= 0.82):
                continue
            boxes.add((x1, top, x2, bottom))
    return sorted(boxes, key=lambda box: ((box[2] - box[0]) * (box[3] - box[1]), box[1], box[0]))


def _nodes(boxes: list[Box], page: OCRPage) -> list[DiagramNode]:
    chosen: list[Box] = []
    for box in boxes:
        # Prefer the smallest complete frame; longer candidates often bridge
        # two stacked cards because their side strokes are nearly aligned.
        if any(_contains(box, other) or _contains(other, box) for other in chosen):
            continue
        chosen.append(box)
    results: list[DiagramNode] = []
    for box in sorted(chosen, key=lambda value: (value[1], value[0])):
        labels = [
            line for line in page.lines
            if line.text.strip() and line.score >= 0.5 and
            box[0] + 4 <= line.box[0] and line.box[2] <= box[2] - 4 and
            box[1] + 4 <= line.box[1] and line.box[3] <= box[3] - 4
        ]
        if not labels:
            continue
        labels.sort(key=lambda line: (line.box[1], line.box[0]))
        results.append(DiagramNode(f"diagram_node_{len(results) + 1}", labels[0].text.strip(), box))
    return results


def _contains(outer: Box, inner: Box) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def _gap_line(ink: np.ndarray, first: Box, second: Box, others: list[Box]) -> float:
    """Return coverage for an uninterrupted horizontal or vertical connector."""
    height, width = ink.shape
    if first[2] + 8 <= second[0] and max(first[1], second[1]) + 12 < min(first[3], second[3]):
        start, stop = first[2] + 2, second[0] - 1
        if stop - start > min(600, width // 2):
            return 0.0
        if any(other[0] < stop and other[2] > start and other[1] < min(first[3], second[3]) and other[3] > max(first[1], second[1]) for other in others):
            return 0.0
        scores = [
            float(ink[max(0, y - 1):min(height, y + 2), start:stop].any(axis=0).mean())
            for y in range(max(first[1], second[1]) + 8, min(first[3], second[3]) - 7)
        ]
    elif first[3] + 8 <= second[1] and max(first[0], second[0]) + 12 < min(first[2], second[2]):
        start, stop = first[3] + 2, second[1] - 1
        if stop - start > min(600, height // 2):
            return 0.0
        if any(other[1] < stop and other[3] > start and other[0] < min(first[2], second[2]) and other[2] > max(first[0], second[0]) for other in others):
            return 0.0
        scores = [
            float(ink[start:stop, max(0, x - 1):min(width, x + 2)].any(axis=1).mean())
            for x in range(max(first[0], second[0]) + 8, min(first[2], second[2]) - 7)
        ]
    else:
        return 0.0
    return max(scores, default=0.0)


def extract_diagram_relations(image, page: OCRPage) -> DiagramRelations:
    """Extract only framed OCR labels joined by a continuous straight line.

    Relations are undirected. Alignment, reading order, color, or dashed guide
    lines alone never create an edge. This can safely return an empty graph.
    """
    ink = _ink_mask(image)
    nodes = _nodes(_framed_boxes(ink), page)
    edges: list[DiagramEdge] = []
    for index, source in enumerate(nodes):
        for target in nodes[index + 1:]:
            other_boxes = [node.box for node in nodes if node.id not in (source.id, target.id)]
            confidence = max(
                _gap_line(ink, source.box, target.box, other_boxes),
                _gap_line(ink, target.box, source.box, other_boxes),
            )
            if confidence >= 0.94:
                edges.append(DiagramEdge(source.id, target.id, "connected", round(confidence, 3)))
    return DiagramRelations(nodes, edges)
