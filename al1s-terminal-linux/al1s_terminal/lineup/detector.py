from pathlib import Path
from typing import Any

import cv2
import numpy as np

from al1s_terminal.providers.yolo_v8 import YoloV8PostProcessor


class PortraitDetector:
    def __init__(self, assets: Path):
        self.rknn = None
        self.backend = "onnx-cpu"
        if (assets / "detector.rknn").exists():
            from al1s_terminal.providers.rknn_yolo import RknnYoloConfig, RknnYoloProvider

            self.rknn = RknnYoloProvider(
                config=RknnYoloConfig(
                    assets / "detector.rknn",
                    640,
                    640,
                    ("portrait",),
                    True,
                )
            )
            if not self.rknn.available:
                raise RuntimeError(self.rknn.error)
            self.backend = "rknn"
        else:
            self.net = cv2.dnn.readNetFromONNX(str(assets / "detector.onnx"))

    def boxes(self, image: np.ndarray) -> list[list[int]]:
        h, w = image.shape[:2]
        ratio = min(640 / w, 640 / h)
        rw, rh = round(w * ratio), round(h * ratio)
        dx, dy = (640 - rw) // 2, (640 - rh) // 2
        canvas = np.full((640, 640, 3), 114, np.uint8)
        canvas[dy : dy + rh, dx : dx + rw] = cv2.resize(image, (rw, rh))
        if self.rknn:
            detections = self.rknn.detect(canvas, {"confidence": 0.45, "nms_threshold": 0.45})[
                "detections"
            ]
            return self._source_boxes(
                [
                    (b[0], b[1], b[0] + b[2], b[1] + b[3])
                    for item in detections
                    for b in [item["box"]]
                ],
                ratio,
                dx,
                dy,
                w,
                h,
            )
        self.net.setInput(cv2.dnn.blobFromImage(canvas, 1 / 255.0, swapRB=True))
        output = self.net.forward()
        boxes, _, _ = YoloV8PostProcessor(input_width=640, input_height=640).decode(
            [output],
            confidence=0.45,
            nms_threshold=0.45,
        )
        return self._source_boxes(boxes, ratio, dx, dy, w, h)

    @staticmethod
    def _source_boxes(
        boxes: Any, ratio: float, dx: int, dy: int, w: int, h: int
    ) -> list[list[int]]:
        result = []
        for x1, y1, x2, y2 in boxes:
            x1, x2 = np.clip((np.array([x1, x2]) - dx) / ratio, 0, w).astype(int)
            y1, y2 = np.clip((np.array([y1, y2]) - dy) / ratio, 0, h).astype(int)
            if x2 > x1 and y2 > y1:
                result.append([int(x1), int(y1), int(x2 - x1), int(y2 - y1)])
        return result


def lineup_boxes(
    boxes: list[list[int]], width: int, height: int
) -> tuple[list[list[int] | None], bool]:
    lower = [
        b
        for b in boxes
        if b[1] + b[3] / 2 > height * 0.58
        and 0.015 * width < b[2] < 0.12 * width
        and 0.4 < b[2] / b[3] < 2
    ]
    left = sorted([b for b in lower if b[0] + b[2] / 2 < width * 0.5], key=lambda b: b[0])
    right = sorted([b for b in lower if b[0] + b[2] / 2 >= width * 0.5], key=lambda b: b[0])
    valid = len(left) == len(right) == 6
    if valid:
        for row in (left, right):
            centers = [b[1] + b[3] / 2 for b in row]
            valid = valid and max(centers) - min(centers) < height * 0.10
    if not valid:
        return [None] * 12, False
    return [box for box in left + right], True
