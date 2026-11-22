"""Contract and reuse tests for the local RapidOCR adapter."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from types import SimpleNamespace
from time import sleep

import numpy as np
from PIL import Image

from app.core.parsing import local_ocr


def test_recognize_accepts_ndarray_and_converts_real_result_arrays(monkeypatch):
    image = np.full((24, 48, 3), 255, dtype=np.uint8)
    result = SimpleNamespace(
        boxes=np.array([[[1, 2], [30, 2], [30, 20], [1, 20]]], dtype=np.float32),
        txts=["课程"],
        scores=np.array([0.98], dtype=np.float32),
    )
    calls = []

    def fake_engine(arg):
        calls.append(arg)
        return result

    monkeypatch.setattr(local_ocr, "_engine", lambda: fake_engine)
    page = local_ocr.recognize(image)

    assert calls == [image]
    assert page.lines[0].text == "课程"
    assert page.lines[0].box == (1.0, 2.0, 30.0, 20.0)
    assert abs(page.lines[0].score - 0.98) < 1e-6


def test_page_engine_reused_across_pages_and_crop_engine_is_independent(monkeypatch):
    import rapidocr

    instances = []

    class FakeRapidOCR:
        def __init__(self):
            self.calls = []
            self.use_det = True
            instances.append(self)

        def __call__(self, image, use_det=None):
            self.calls.append(use_det)
            if use_det is not None:
                self.use_det = use_det
            if self.use_det:
                return SimpleNamespace(
                    boxes=np.array([[[1, 1], [8, 1], [8, 8], [1, 8]]]),
                    txts=["整页"],
                    scores=np.array([0.99]),
                )
            return SimpleNamespace(txts=["单格"], scores=np.array([0.98]))

    monkeypatch.setattr(rapidocr, "RapidOCR", FakeRapidOCR)
    local_ocr._engine.cache_clear()
    local_ocr._recognizer.cache_clear()
    try:
        image = Image.new("RGB", (24, 24), "white")
        assert local_ocr.recognize(image).text == "整页"
        assert local_ocr.recognize_crop(image)[0] == "单格"
        assert local_ocr.recognize(image).text == "整页"
        assert len(instances) == 2
        assert instances[0].calls == [None, None]
        assert instances[1].calls == [False]
        assert instances[0].use_det is True
    finally:
        local_ocr._engine.cache_clear()
        local_ocr._recognizer.cache_clear()


def test_crop_accepts_ndarray(monkeypatch):
    image = np.full((20, 40, 3), 255, dtype=np.uint8)
    calls = []

    def fake_recognizer(arg, use_det):
        calls.append((arg, use_det))
        return SimpleNamespace(txts=["学分"], scores=np.array([0.99]))

    monkeypatch.setattr(local_ocr, "_recognizer", lambda: fake_recognizer)
    assert local_ocr.recognize_crop(image) == ("学分", 0.99)
    assert calls[0][0] is image
    assert calls[0][1] is False


def test_concurrent_page_requests_share_one_engine_without_overlapping_calls(monkeypatch):
    import rapidocr

    barrier = Barrier(4)
    counter_lock = Lock()
    instances = []
    active = 0
    peak_active = 0

    class FakeRapidOCR:
        def __init__(self):
            sleep(0.03)
            with counter_lock:
                instances.append(self)

        def __call__(self, image):
            nonlocal active, peak_active
            with counter_lock:
                active += 1
                peak_active = max(peak_active, active)
            sleep(0.03)
            with counter_lock:
                active -= 1
            return SimpleNamespace(boxes=None, txts=None, scores=None)

    monkeypatch.setattr(rapidocr, "RapidOCR", FakeRapidOCR)
    local_ocr._engine.cache_clear()
    try:
        def run_one():
            barrier.wait()
            return local_ocr.recognize(np.full((16, 16, 3), 255, dtype=np.uint8))

        with ThreadPoolExecutor(max_workers=4) as executor:
            pages = list(executor.map(lambda _: run_one(), range(4)))
        assert all(page.lines == [] for page in pages)
        assert len(instances) == 1
        assert peak_active == 1
    finally:
        local_ocr._engine.cache_clear()


def test_concurrent_crop_requests_share_one_recognizer_without_overlapping_calls(monkeypatch):
    import rapidocr

    barrier = Barrier(4)
    counter_lock = Lock()
    instances = []
    active = 0
    peak_active = 0

    class FakeRapidOCR:
        def __init__(self):
            sleep(0.03)
            with counter_lock:
                instances.append(self)

        def __call__(self, image, use_det=None):
            nonlocal active, peak_active
            assert use_det is False
            with counter_lock:
                active += 1
                peak_active = max(peak_active, active)
            sleep(0.03)
            with counter_lock:
                active -= 1
            return SimpleNamespace(txts=["学分"], scores=[0.99])

    monkeypatch.setattr(rapidocr, "RapidOCR", FakeRapidOCR)
    local_ocr._recognizer.cache_clear()
    try:
        def run_one():
            barrier.wait()
            return local_ocr.recognize_crop(np.full((16, 16, 3), 255, dtype=np.uint8))

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: run_one(), range(4)))
        assert results == [("学分", 0.99)] * 4
        assert len(instances) == 1
        assert peak_active == 1
    finally:
        local_ocr._recognizer.cache_clear()


def test_local_fixture_handles_actual_rapidocr_ndarray_result():
    from pathlib import Path

    import rapidocr
    model_dir = Path(rapidocr.__file__).parent / "models"
    models = (
        "PP-OCRv6_det_small.onnx",
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "PP-OCRv6_rec_small.onnx",
    )
    assert all((model_dir / name).exists() for name in models), "RapidOCR's local default models are unavailable"
    path = Path(__file__).resolve().parents[2] / "docs" / "ppstructure-subset-evidence" / "valid-table" / "T_table_valid.png"
    assert path.exists(), "valid table benchmark fixture is unavailable"
    with Image.open(path) as image:
        raw_result = local_ocr._engine()(np.asarray(image.convert("RGB")))
        page = local_ocr.recognize(image)
    assert isinstance(raw_result.boxes, np.ndarray)
    assert len(raw_result.scores) == len(raw_result.boxes)
    assert len(page.lines) >= 20
    assert any(line.text == "4" for line in page.lines)
    assert all(isinstance(line.box, tuple) and len(line.box) == 4 for line in page.lines)
    assert page.seconds >= 0
