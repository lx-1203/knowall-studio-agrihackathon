"""Compare ASCII and typeset crops with the already cached local formula model."""

import argparse
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from time import perf_counter

parser = argparse.ArgumentParser()
parser.add_argument("--image", type=Path, required=True)
parser.add_argument("--model-dir", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
if not (args.model_dir / "inference.pdiparams").is_file():
    raise SystemExit("Cached formula model is missing")

os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "False"
import torch  # noqa: E402  - Windows DLL initialization order
from PIL import Image  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import mathtext  # noqa: E402
from paddleocr import FormulaRecognitionPipeline  # noqa: E402

image = Image.open(args.image).convert("RGB")
crop = image.crop((95, 180, 680, 255))
math_latex = r"x=\frac{-b\pm\sqrt{b^2-4ac}}{2a}"
math_buffer = BytesIO()
mathtext.math_to_image(f"${math_latex}$", math_buffer, dpi=200, format="png")
math_bytes = math_buffer.getvalue()
math_image = Image.open(BytesIO(math_bytes)).convert("RGB")
started = perf_counter()
pipeline = FormulaRecognitionPipeline(
    use_layout_detection=False,
    use_doc_orientation_classify=False,
    use_doc_unwarping=False,
    formula_recognition_model_name="PP-FormulaNet_plus-L",
    formula_recognition_model_dir=str(args.model_dir),
    device="cpu",
)
initialized = perf_counter() - started
cases = []
for name, candidate, digest, expectation in (
    ("ascii_synthetic_crop", crop, sha256(args.image.read_bytes()).hexdigest(),
     "x = (-b +/- sqrt(b^2 - 4ac)) / (2a)"),
    ("mathtext_rendered", math_image, sha256(math_bytes).hexdigest(), math_latex),
):
    started = perf_counter()
    results = pipeline.predict(input=np.asarray(candidate), use_layout_detection=False)
    cases.append({"name": name, "image_sha256": digest, "image_size": candidate.size,
                  "expected": expectation, "inference_seconds": perf_counter() - started,
                  "result": [result.json for result in results]})
payload = {"model": "PP-FormulaNet_plus-L", "device": "cpu",
           "initialization_seconds": initialized, "cases": cases,
           "limitations": ["The ASCII source is plain text, not typeset mathematics.",
                           "The mathtext source is one clean synthetic formula, not a scanned textbook page.",
                           "Model output has no confidence score and was not independently verified."]}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, default=str))
