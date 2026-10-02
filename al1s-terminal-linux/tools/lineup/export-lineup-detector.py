from pathlib import Path

import torch
from ultralytics import YOLO

root = Path(__file__).resolve().parents[4]
original = torch.onnx.export


def legacy_export(*args, **kwargs):
    kwargs["dynamo"] = False
    return original(*args, **kwargs)


torch.onnx.export = legacy_export
model = YOLO(str(root / ".tool-cache/lineup-training/detector/weights/best.pt"))
path = model.export(format="onnx", imgsz=640, opset=12, simplify=False)
(root / ".terminal-assets/lineup-v1/detector.onnx").write_bytes(Path(path).read_bytes())
