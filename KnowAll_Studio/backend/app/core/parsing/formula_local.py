"""Optional local formula suggestions; original OCR text remains authoritative."""

from dataclasses import dataclass
from functools import lru_cache
from math import ceil, floor
import os
from pathlib import Path
import re
from threading import Lock
from typing import Sequence


@dataclass(frozen=True)
class FormulaCandidate:
    ocr_text: str
    box: tuple[int, int, int, int]


@dataclass(frozen=True)
class FormulaSuggestion:
    ocr_text: str
    latex: str
    box: tuple[int, int, int, int]
    verified: bool = False


_STRUCTURE = re.compile(r"\^|/|sqrt|integral|sum|[∫∑√]", re.IGNORECASE)
_CALCULUS = re.compile(r"[∫∑√]")
_OPERATOR = re.compile(r"[\d=+\-*/^()]")
_MODEL_LOCK = Lock()


def _touches(a, b) -> bool:
    ax0, ay0, ax1, ay1 = a.box
    bx0, by0, bx1, by1 = b.box
    x_gap = max(0, ax0 - bx1, bx0 - ax1)
    y_gap = max(0, ay0 - by1, by0 - ay1)
    height = max(ay1 - ay0, by1 - by0)
    return y_gap <= 1 and x_gap <= max(12, height * 1.25)


def _groups(lines: Sequence) -> list[list]:
    """Keep nearby OCR fragments together, including stacked fraction pieces."""
    remaining = set(range(len(lines)))
    groups = []
    while remaining:
        first = min(remaining)
        remaining.remove(first)
        group_indices = [first]
        for index in group_indices:
            for other in sorted(remaining):
                if not _touches(lines[index], lines[other]) or len(group_indices) >= 8:
                    continue
                proposed = group_indices + [other]
                top = min(lines[item].box[1] for item in proposed)
                bottom = max(lines[item].box[3] for item in proposed)
                tallest = max(lines[item].box[3] - lines[item].box[1] for item in proposed)
                if bottom - top > tallest * 1.85:
                    continue
                group_indices.append(other)
                remaining.remove(other)
        groups.append([lines[index] for index in sorted(group_indices)])
    return groups


def locate_formula_candidates(lines: Sequence, page_size: tuple[int, int]) -> list[FormulaCandidate]:
    """Find high-signal formula lines from existing OCR boxes, without another model pass."""
    width, height = page_size
    candidates = []
    for group in _groups(lines):
        value = " ".join(line.text.strip() for line in group if line.text.strip())
        if not value or len(value) > 180 or value.lower().startswith(("http://", "https://")):
            continue
        if not ((_CALCULUS.search(value) and _OPERATOR.search(value))
                or ("=" in value and _STRUCTURE.search(value))):
            continue
        x0 = min(line.box[0] for line in group)
        y0 = min(line.box[1] for line in group)
        x1 = max(line.box[2] for line in group)
        y1 = max(line.box[3] for line in group)
        box = (max(0, floor(x0) - 6), max(0, floor(y0) - 6),
               min(width, ceil(x1) + 6), min(height, ceil(y1) + 6))
        if box[2] > box[0] and box[3] > box[1]:
            candidates.append(FormulaCandidate(value, box))
    return candidates


@lru_cache(maxsize=2)
def _pipeline(model_dir: str):
    # Import optional PyTorch first when installed to avoid Windows DLL conflicts.
    os.environ["FLAGS_use_mkldnn"] = "0"
    os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "False"
    try:
        import torch  # noqa: F401, PLC0415
    except ImportError:
        pass
    from paddleocr import FormulaRecognitionPipeline

    return FormulaRecognitionPipeline(
        use_layout_detection=False,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        formula_recognition_model_name="PP-FormulaNet_plus-L",
        formula_recognition_model_dir=model_dir,
        device="cpu",
    )


def suggest_formula(image, candidate: FormulaCandidate, model_dir: Path, *, predictor=None) -> FormulaSuggestion | None:
    """Return an unverified LaTeX suggestion for an explicitly selected candidate.

    This does not edit OCR text. A caller must decide whether to display or use the
    suggestion after checking it against the page image.
    """
    if predictor is None:
        model_path = Path(model_dir)
        if not (model_path / "inference.pdiparams").is_file():
            raise FileNotFoundError(f"cached formula model is missing: {model_path}")

    import numpy as np

    crop = image.crop(candidate.box).convert("RGB")
    crop_array = np.asarray(crop)
    with _MODEL_LOCK:
        if predictor is None:
            predictor = _pipeline(str(model_path.resolve()))
        results = list(predictor.predict(input=crop_array, use_layout_detection=False))
    for result in results:
        formulas = result.json.get("res", {}).get("formula_res_list", [])
        for item in formulas:
            latex = item.get("rec_formula", "").strip()
            if latex:
                return FormulaSuggestion(candidate.ocr_text, latex, candidate.box)
    return None
