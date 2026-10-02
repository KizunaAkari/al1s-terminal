from __future__ import annotations

import importlib.metadata
import os
import platform
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from al1s_terminal.providers.yolo_v8 import YoloV8PostProcessor

COCO80_LABELS = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)


@dataclass(frozen=True, slots=True)
class RknnYoloConfig:
    model_path: Path
    input_width: int
    input_height: int
    labels: tuple[str, ...]
    scores_are_logits: bool

    @classmethod
    def from_environment(cls) -> RknnYoloConfig:
        model_path = Path(
            os.getenv("AL1S_TERMINAL_RKNN_MODEL", "/models/yolov8n_640x640_rk3576.rknn")
        )
        input_width, input_height = _input_size(
            os.getenv("AL1S_TERMINAL_RKNN_INPUT_SIZE", "640x640")
        )
        labels = _load_labels(os.getenv("AL1S_TERMINAL_RKNN_LABELS", "").strip())
        scores_are_logits = os.getenv("AL1S_TERMINAL_RKNN_SCORES_ARE_LOGITS", "0") == "1"
        return cls(model_path, input_width, input_height, labels, scores_are_logits)


class RknnYoloProvider:
    """On-demand RKNN Lite YOLOv8 provider for the RK3576 terminal."""

    def __init__(
        self,
        *,
        config: RknnYoloConfig | None = None,
        runtime_factory: Callable[[], Any] | None = None,
        cv2_module: Any | None = None,
    ) -> None:
        self.error = ""
        self.decoder = "auto"
        self._runtime: Any | None = None
        self._lock = threading.Lock()
        self._config = config
        self._cv2 = cv2_module
        try:
            self._config = config or RknnYoloConfig.from_environment()
            if not self._config.model_path.is_file():
                raise FileNotFoundError(
                    f"RKNN YOLO model does not exist: {self._config.model_path}"
                )
            if self._cv2 is None:
                import cv2

                self._cv2 = cv2
            factory = runtime_factory or _runtime_factory
            self._runtime = factory()
            self._require_success(
                self._runtime.load_rknn(str(self._config.model_path)),
                "load_rknn",
            )
            self._require_success(self._runtime.init_runtime(), "init_runtime")
        except Exception as exc:
            self.error = str(exc)
            self.close()

    @property
    def available(self) -> bool:
        return self._runtime is not None and not self.error

    def status(self) -> dict[str, Any]:
        config = self._config
        return {
            "available": self.available,
            "model": str(config.model_path) if config else None,
            "input_size": [config.input_width, config.input_height] if config else None,
            "labels": len(config.labels) if config else None,
            "decoder": self.decoder,
            "runtime_version": _package_version("rknn-toolkit-lite2"),
            "runtime_library_version": (
                os.getenv("AL1S_TERMINAL_RKNN_RUNTIME_VERSION", "").strip() or None
            ),
            "architecture": platform.machine(),
            "error": self.error or None,
        }

    def detect(self, image: Any, params: dict[str, Any]) -> dict[str, Any]:
        runtime, config = self._require_available()
        cv2 = self._require_cv2()
        source = self._read_image(image)
        prepared, ratio, padding = self._letterbox(source, config)
        rgb = cv2.cvtColor(prepared, cv2.COLOR_BGR2RGB)
        runtime_input = self.runtime_input(rgb)
        started = time.perf_counter()
        with self._lock:
            raw_outputs = runtime.inference(inputs=[runtime_input])
        inference_ms = round((time.perf_counter() - started) * 1_000, 3)
        if not raw_outputs:
            raise RuntimeError("RKNN inference returned no output tensors")

        confidence = _float_param(params, "confidence", "threshold", default=0.25)
        nms_threshold = _float_param(params, "nms_threshold", default=0.45)
        processor = YoloV8PostProcessor(
            input_width=config.input_width,
            input_height=config.input_height,
            scores_are_logits=config.scores_are_logits,
        )
        boxes, classes, scores = processor.decode(
            raw_outputs,
            confidence=confidence,
            nms_threshold=nms_threshold,
        )
        self.decoder = (
            "ultralytics_yolov8_flat" if len(raw_outputs) == 1 else "rockchip_model_zoo_yolov8"
        )
        detections = self._detections(
            boxes,
            classes,
            scores,
            source.shape[:2],
            ratio,
            padding,
            params,
        )
        return {
            "detections": detections,
            "inference_ms": inference_ms,
            "model": str(config.model_path),
            "decoder": self.decoder,
            "output_shapes": [list(np.asarray(output).shape) for output in raw_outputs],
        }

    def _require_available(self) -> tuple[Any, RknnYoloConfig]:
        if not self.available or self._runtime is None or self._config is None:
            raise RuntimeError(self.error or "RKNN YOLO provider is unavailable")
        return self._runtime, self._config

    def _read_image(self, image: Any) -> np.ndarray:
        cv2 = self._require_cv2()
        if isinstance(image, str | Path):
            source = cv2.imread(str(image), cv2.IMREAD_COLOR)
            if source is None:
                raise ValueError(f"YOLO image could not be read: {image}")
        else:
            source = np.asarray(image)
        if source.ndim != 3 or source.shape[2] < 3:
            raise ValueError(f"YOLO expects an HWC BGR image, got {source.shape}")
        return np.ascontiguousarray(source[:, :, :3])

    def _letterbox(
        self,
        image: np.ndarray,
        config: RknnYoloConfig,
    ) -> tuple[np.ndarray, float, tuple[float, float]]:
        cv2 = self._require_cv2()
        height, width = image.shape[:2]
        ratio = min(config.input_width / width, config.input_height / height)
        resized_width = round(width * ratio)
        resized_height = round(height * ratio)
        resized = cv2.resize(
            image,
            (resized_width, resized_height),
            interpolation=cv2.INTER_LINEAR,
        )
        dw = (config.input_width - resized_width) / 2
        dh = (config.input_height - resized_height) / 2
        result = cv2.copyMakeBorder(
            resized,
            round(dh - 0.1),
            round(dh + 0.1),
            round(dw - 0.1),
            round(dw + 0.1),
            cv2.BORDER_CONSTANT,
            value=(0, 0, 0),
        )
        return result, ratio, (dw, dh)

    def _require_cv2(self) -> Any:
        if self._cv2 is None:
            raise RuntimeError("OpenCV is unavailable")
        return self._cv2

    def _detections(
        self,
        boxes: np.ndarray,
        classes: np.ndarray,
        scores: np.ndarray,
        source_shape: tuple[int, int],
        ratio: float,
        padding: tuple[float, float],
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not boxes.size or self._config is None:
            return []
        restored = YoloV8PostProcessor.restore_boxes(
            boxes,
            source_shape=source_shape,
            ratio=ratio,
            padding=padding,
        )
        allowed_ids = _allowed_class_ids(params, self._config.labels)
        detections = [
            _detection(box, int(class_id), float(score), self._config.labels)
            for box, class_id, score in zip(restored, classes, scores, strict=True)
            if allowed_ids is None or int(class_id) in allowed_ids
        ]
        detections.sort(key=lambda item: float(item["confidence"]), reverse=True)
        maximum = max(1, min(1_000, int(params.get("max_detections", 100))))
        return detections[:maximum]

    @staticmethod
    def runtime_input(image: np.ndarray) -> np.ndarray:
        if image.ndim != 3:
            raise ValueError(f"RKNN YOLO input must be HWC before batching, got {image.shape}")
        return np.ascontiguousarray(np.expand_dims(image, axis=0))

    @staticmethod
    def _require_success(result: Any, operation: str) -> None:
        if result != 0:
            raise RuntimeError(f"RKNN {operation} failed with code {result}")

    def close(self) -> None:
        runtime = self._runtime
        self._runtime = None
        if runtime is not None:
            with suppress(Exception):
                runtime.release()

    def __del__(self) -> None:
        self.close()


def _runtime_factory() -> Any:
    from rknnlite.api import RKNNLite

    return RKNNLite(verbose=False)


def _input_size(value: str) -> tuple[int, int]:
    normalized = value.lower().replace("*", "x").replace(",", "x")
    parts = [part.strip() for part in normalized.split("x") if part.strip()]
    if len(parts) == 1:
        parts *= 2
    if len(parts) != 2:
        raise ValueError("AL1S_TERMINAL_RKNN_INPUT_SIZE must look like 640x640")
    width, height = (int(part) for part in parts)
    if width < 1 or height < 1:
        raise ValueError("AL1S_TERMINAL_RKNN_INPUT_SIZE must be positive")
    return width, height


def _load_labels(configured: str) -> tuple[str, ...]:
    if not configured:
        return COCO80_LABELS
    path = Path(configured)
    if not path.is_file():
        raise FileNotFoundError(f"YOLO labels file does not exist: {path}")
    labels = tuple(
        line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    if not labels:
        raise ValueError(f"YOLO labels file is empty: {path}")
    return labels


def _float_param(params: dict[str, Any], *names: str, default: float) -> float:
    value: Any = next((params[name] for name in names if name in params), default)
    result = float(value)
    if not 0 <= result <= 1:
        raise ValueError(f"{names[0]} must be between 0 and 1")
    return result


def _allowed_class_ids(params: dict[str, Any], labels: tuple[str, ...]) -> set[int] | None:
    raw_ids = params.get("class_ids", params.get("class_id"))
    if raw_ids is not None and raw_ids != "":
        values = raw_ids if isinstance(raw_ids, list) else [raw_ids]
        return {int(value) for value in values}
    raw_names = params.get("class_names", params.get("class_name"))
    if raw_names is None or raw_names == "":
        return None
    values = raw_names if isinstance(raw_names, list) else [raw_names]
    lookup = {label: index for index, label in enumerate(labels)}
    missing = [str(value) for value in values if str(value) not in lookup]
    if missing:
        raise ValueError(f"unknown YOLO class names: {', '.join(missing)}")
    return {lookup[str(value)] for value in values}


def _detection(
    box: np.ndarray,
    class_id: int,
    confidence: float,
    labels: tuple[str, ...],
) -> dict[str, Any]:
    x1, y1, x2, y2 = (float(value) for value in box)
    return {
        "class_id": class_id,
        "label": labels[class_id] if class_id < len(labels) else str(class_id),
        "confidence": round(confidence, 6),
        "xyxy": [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)],
        "box": [round(x1, 2), round(y1, 2), round(x2 - x1, 2), round(y2 - y1, 2)],
    }


def _package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
