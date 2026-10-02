"""Convert the trained portrait detector without quantization or changing its labels."""

import argparse
import hashlib
import importlib.metadata
import json
import tempfile
from pathlib import Path

import onnx
from rknn.api import RKNN

parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
version = importlib.metadata.version("rknn-toolkit2")
if version != "2.3.2":
    raise RuntimeError("Use the pinned RKNN conversion toolchain (2.3.2).")
model = RKNN(verbose=False)
temporary = tempfile.TemporaryDirectory(prefix="lineup-rknn-")
try:
    # Keep DFL/anchor decoding in the existing FP32 postprocessor. Converting
    # the flat head's decoding graph to FP16 shifts small portrait boundaries.
    heads = [f"/model.22/cv{k}.{i}/cv{k}.{i}.2/Conv_output_0" for i in range(3) for k in (2, 3)]
    extracted = str(Path(temporary.name) / "heads.onnx")
    onnx.utils.extract_model(str(args.source), extracted, ["images"], heads)
    model.config(target_platform="rk3576", mean_values=[[0, 0, 0]], std_values=[[255, 255, 255]])
    for name, operation in (
        ("load", lambda: model.load_onnx(model=extracted)),
        ("build", lambda: model.build(do_quantization=False)),
        ("export", lambda: model.export_rknn(str(args.output))),
    ):
        code = operation()
        if code != 0:
            raise RuntimeError(f"RKNN {name} failed: {code}")
    args.output.with_suffix(".rknn.json").write_text(
        json.dumps(
            {
                "target": "rk3576",
                "toolkit": version,
                "precision": "fp16",
                "input": "RGB uint8 NHWC 1x640x640x3; letterbox 114; normalization /255",
                "output": (
                    "6 NCHW tensors: DFL logits, class logits at strides 8/16/32; FP32 CPU decode"
                ),
                "source_sha256": hashlib.sha256(args.source.read_bytes()).hexdigest(),
                "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
finally:
    model.release()
    temporary.cleanup()
