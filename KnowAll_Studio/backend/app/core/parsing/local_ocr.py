"""Reusable, local OCR for image and rasterized PDF pages."""

from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from time import perf_counter


_page_lock = Lock()
_crop_lock = Lock()


@dataclass(frozen=True)
class OCRLine:
    text: str
    box: tuple[float, float, float, float]
    score: float


@dataclass(frozen=True)
class OCRPage:
    lines: list[OCRLine]
    seconds: float

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines if line.text.strip())


@lru_cache(maxsize=1)
def _engine():
    from rapidocr import RapidOCR

    return RapidOCR()


@lru_cache(maxsize=1)
def _recognizer():
    from rapidocr import RapidOCR

    return RapidOCR()


def _image_array(image):
    import numpy as np

    if hasattr(image, "convert"):
        return np.asarray(image.convert("RGB"))
    return np.asarray(image)


def recognize(image) -> OCRPage:
    started = perf_counter()
    image_array = _image_array(image)
    with _page_lock:
        result = _engine()(image_array)
    lines = []
    boxes = result.boxes if result.boxes is not None else []
    texts = result.txts if result.txts is not None else []
    scores = result.scores if result.scores is not None else []
    for box, value, score in zip(boxes, texts, scores):
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        lines.append(OCRLine(str(value), (min(xs), min(ys), max(xs), max(ys)), float(score)))
    return OCRPage(lines, perf_counter() - started)


def recognize_crop(image) -> tuple[str, float]:
    """Recognize one known text cell without running a page detector again."""
    image_array = _image_array(image)
    with _crop_lock:
        result = _recognizer()(image_array, use_det=False)
    texts = result.txts if result.txts is not None else []
    scores = result.scores if result.scores is not None else []
    if len(texts) == 0:
        return "", 0.0
    return str(texts[0]), float(scores[0])
