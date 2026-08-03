from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test the Maa RKNN YOLO provider")
    parser.add_argument("--model", required=True, help="RKNN YOLOv8 model path")
    args = parser.parse_args()
    os.environ["MAA_YOLO_MODEL"] = args.model

    from agent.rknn_yolo import RknnYoloProvider
    import cv2

    provider = RknnYoloProvider()
    if not provider.available:
        raise RuntimeError(provider.error)
    try:
        image = np.zeros((640, 640, 3), dtype=np.uint8)
        result = provider.detect(image, {"confidence": 0.99, "max_detections": 1})
        print(json.dumps({
            "status": "ok",
            "runtime": provider.status(),
            "inference_ms": result["inference_ms"],
            "output_shapes": result["output_shapes"],
            "decoder": result["decoder"],
            "dependencies": {
                "python": platform.python_version(),
                "architecture": platform.machine(),
                "rknn_toolkit_lite2": importlib.metadata.version("rknn-toolkit-lite2"),
                "numpy": np.__version__,
                "opencv": cv2.__version__,
            },
        }, ensure_ascii=False))
        return 0
    finally:
        provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
