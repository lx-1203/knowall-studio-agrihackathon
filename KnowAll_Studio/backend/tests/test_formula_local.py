"""Conservative formula candidates and optional local suggestions."""

import numpy as np
import pytest
from PIL import Image
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from time import sleep
from types import ModuleType

from app.core.parsing.local_ocr import OCRLine


def test_candidates_require_math_structure_and_keep_bounded_crop():
    from app.core.parsing.formula_local import locate_formula_candidates

    lines = [
        OCRLine("本章讲授函数和积分", (5, 4, 130, 24), 0.99),
        OCRLine("本章介绍∫的性质", (5, 24, 130, 30), 0.99),
        OCRLine("学分 = 5", (10, 32, 88, 52), 0.99),
        OCRLine("x = (-b +/- sqrt(b^2 - 4ac)) / (2a)", (3, 65, 260, 88), 0.99),
        OCRLine("e^(i*pi) + 1 = 0", (15, 96, 150, 119), 0.99),
    ]

    candidates = locate_formula_candidates(lines, (280, 130))

    assert [candidate.ocr_text for candidate in candidates] == [lines[3].text, lines[4].text]
    assert candidates[0].box == (0, 59, 266, 94)
    assert candidates[1].box == (9, 90, 156, 125)


def test_suggestion_keeps_ocr_text_and_does_not_assert_verification(tmp_path):
    from app.core.parsing.formula_local import FormulaCandidate, suggest_formula

    calls = []

    class Predictor:
        def predict(self, *, input, use_layout_detection):
            calls.append((input.shape, use_layout_detection))
            return [type("Result", (), {"json": {"res": {"formula_res_list": [
                {"rec_formula": r"x=\frac{1}{2}"}
            ]}}})()]

    candidate = FormulaCandidate("x = 1 / 2", (10, 20, 90, 45))
    suggestion = suggest_formula(Image.new("RGB", (120, 80), "white"), candidate,
                                 tmp_path, predictor=Predictor())

    assert suggestion is not None
    assert suggestion.ocr_text == "x = 1 / 2"
    assert suggestion.latex == r"x=\frac{1}{2}"
    assert suggestion.box == candidate.box
    assert suggestion.verified is False
    assert calls == [((25, 80, 3), False)]


def test_fraction_fragments_form_one_candidate_crop():
    from app.core.parsing.formula_local import locate_formula_candidates

    # Actual RapidOCR boxes from one typeset quadratic formula.
    lines = [
        OCRLine("−b±√", (47, 5, 121, 31), 0.95),
        OCRLine("b²", (111, 2, 149, 31), 0.95),
        OCRLine("- 4ac", (143, 4, 207, 33), 0.95),
        OCRLine("X", (0, 19, 22, 40), 0.95),
        OCRLine("二", (18, 16, 51, 40), 0.95),
        OCRLine("2a", (114, 31, 142, 51), 0.95),
    ]

    candidates = locate_formula_candidates(lines, (208, 52))

    assert len(candidates) == 1
    assert candidates[0].box == (0, 0, 208, 52)
    assert "√" in candidates[0].ocr_text


def test_touching_prose_lines_do_not_extend_formula_crop():
    from app.core.parsing.formula_local import locate_formula_candidates

    lines = [
        OCRLine("x = sqrt(2)", (10, 10, 120, 30), 0.99),
        OCRLine("下面开始正文", (10, 29, 130, 49), 0.99),
        OCRLine("继续讲解内容", (10, 48, 130, 68), 0.99),
    ]

    candidates = locate_formula_candidates(lines, (150, 90))

    assert len(candidates) == 1
    assert candidates[0].box == (4, 4, 126, 36)
    assert candidates[0].ocr_text == "x = sqrt(2)"


def test_missing_local_model_fails_before_loading(tmp_path):
    from app.core.parsing.formula_local import FormulaCandidate, suggest_formula

    with pytest.raises(FileNotFoundError, match="cached formula model"):
        suggest_formula(Image.new("RGB", (30, 30)),
                        FormulaCandidate("x^2 = 1", (0, 0, 20, 20)), tmp_path)


def test_empty_model_result_is_not_a_suggestion(tmp_path):
    from app.core.parsing.formula_local import FormulaCandidate, suggest_formula

    class Predictor:
        def predict(self, *, input, use_layout_detection):
            assert isinstance(input, np.ndarray)
            return [type("Result", (), {"json": {"res": {"formula_res_list": []}}})()]

    assert suggest_formula(Image.new("RGB", (30, 30)),
                           FormulaCandidate("x^2 = 1", (0, 0, 20, 20)),
                           tmp_path, predictor=Predictor()) is None


def test_pipeline_can_start_without_optional_torch(monkeypatch, tmp_path):
    from app.core.parsing.formula_local import _pipeline

    fake_paddleocr = ModuleType("paddleocr")

    class Pipeline:
        def __init__(self, **kwargs):
            self.options = kwargs

    fake_paddleocr.FormulaRecognitionPipeline = Pipeline
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "paddleocr", fake_paddleocr)
    _pipeline.cache_clear()
    try:
        pipeline = _pipeline(str(tmp_path))
        assert pipeline.options["formula_recognition_model_dir"] == str(tmp_path)
        assert pipeline.options["device"] == "cpu"
    finally:
        _pipeline.cache_clear()


def test_concurrent_suggestions_share_model_and_serialize_predict(monkeypatch, tmp_path):
    from app.core.parsing.formula_local import FormulaCandidate, _pipeline, suggest_formula

    (tmp_path / "inference.pdiparams").write_bytes(b"test")
    fake_paddleocr = ModuleType("paddleocr")
    counters = {"created": 0, "active": 0, "max_active": 0}
    counter_lock = Lock()

    class Pipeline:
        def __init__(self, **kwargs):
            sleep(0.05)
            with counter_lock:
                counters["created"] += 1

        def predict(self, *, input, use_layout_detection):
            with counter_lock:
                counters["active"] += 1
                counters["max_active"] = max(counters["max_active"], counters["active"])
            sleep(0.05)
            with counter_lock:
                counters["active"] -= 1
            return [type("Result", (), {"json": {"res": {"formula_res_list": [
                {"rec_formula": "x=1"}
            ]}}})()]

    fake_paddleocr.FormulaRecognitionPipeline = Pipeline
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "paddleocr", fake_paddleocr)
    _pipeline.cache_clear()
    start = Barrier(2)

    def run_one(_):
        start.wait()
        return suggest_formula(Image.new("RGB", (30, 30)),
                               FormulaCandidate("x^2=1", (0, 0, 20, 20)), tmp_path)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            suggestions = list(pool.map(run_one, range(2)))
        assert [suggestion.latex for suggestion in suggestions] == ["x=1", "x=1"]
        assert counters["created"] == 1
        assert counters["max_active"] == 1
    finally:
        _pipeline.cache_clear()
