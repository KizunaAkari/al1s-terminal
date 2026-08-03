from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np


# Post-processing follows Rockchip's Apache-2.0 RKNN Model Zoo YOLOv8 demo:
# https://github.com/airockchip/rknn_model_zoo/tree/v2.3.2/examples/yolov8
# The DFL implementation is NumPy-only so a 2 GB terminal does not need PyTorch.
COCO_LABELS = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange", "broccoli",
    "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard",
    "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
    "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)

DEFAULT_MODELS = (
    "/run/media/mmcblk1p8/workspace/yolov8/yolov8n_640x640_rk3576.rknn",
    "/run/media/mmcblk1p8/workspace/yolov5/bin/yolov8n_640x640_rk3576.rknn",
)


class RknnYoloProvider:
    """RKNN Lite YOLOv8 provider used by MaaProjectYolo recognition nodes."""

    def __init__(self) -> None:
        self.model_path = self._select_model(os.getenv("MAA_YOLO_MODEL", "").strip())
        self.input_width, self.input_height = self._input_size(
            os.getenv("MAA_YOLO_INPUT_SIZE", "640x640")
        )
        self.labels = self._load_labels(os.getenv("MAA_YOLO_LABELS", "").strip())
        self.error = ""
        self.decoder = "auto"
        self._rknn: Any = None
        self._lock = threading.Lock()
        try:
            import cv2
            from rknnlite.api import RKNNLite

            self._cv2 = cv2
            self._rknn = RKNNLite(verbose=False)
            result = self._rknn.load_rknn(str(self.model_path))
            if result != 0:
                raise RuntimeError(f"RKNN load_rknn failed with code {result}")
            result = self._rknn.init_runtime()
            if result != 0:
                raise RuntimeError(f"RKNN init_runtime failed with code {result}")
        except Exception as exc:
            self.error = str(exc)
            if self._rknn is not None:
                try:
                    self._rknn.release()
                except Exception:
                    pass
                self._rknn = None

    @property
    def available(self) -> bool:
        return self._rknn is not None and not self.error

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "model": str(self.model_path),
            "input_size": [self.input_width, self.input_height],
            "labels": len(self.labels),
            "decoder": self.decoder,
            "runtime_version": os.getenv("MAA_RKNN_RUNTIME_VERSION", "").strip() or None,
            "error": self.error or None,
        }

    def detect(self, image: Any, params: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError(self.error or "RKNN YOLO provider is unavailable")
        if isinstance(image, (str, Path)):
            source = self._cv2.imread(str(image), self._cv2.IMREAD_COLOR)
            if source is None:
                raise ValueError(f"YOLO image could not be read: {image}")
        else:
            source = np.asarray(image)
        if source.ndim != 3 or source.shape[2] < 3:
            raise ValueError(f"YOLO expects an HWC BGR image, got {source.shape}")
        source = np.ascontiguousarray(source[:, :, :3])

        prepared, ratio, padding = self._letterbox(source)
        rgb = self._cv2.cvtColor(prepared, self._cv2.COLOR_BGR2RGB)
        runtime_input = self._runtime_input(rgb)
        started = time.perf_counter()
        with self._lock:
            outputs = self._rknn.inference(inputs=[runtime_input])
        inference_ms = round((time.perf_counter() - started) * 1_000, 3)
        if not outputs:
            raise RuntimeError("RKNN inference returned no output tensors")

        confidence = self._float_param(params, "confidence", "threshold", default=0.25)
        nms_threshold = self._float_param(params, "nms_threshold", default=0.45)
        decoder = (
            "ultralytics_yolov8_flat"
            if len(outputs) == 1
            else "rockchip_model_zoo_yolov8"
        )
        boxes, classes, scores = self._post_process(outputs, confidence, nms_threshold)
        self.decoder = decoder
        detections: list[dict[str, Any]] = []
        if boxes.size:
            boxes = self._restore_boxes(boxes, source.shape[:2], ratio, padding)
            allowed_ids = self._allowed_class_ids(params)
            for box, class_id, score in zip(boxes, classes, scores):
                class_index = int(class_id)
                if allowed_ids is not None and class_index not in allowed_ids:
                    continue
                x1, y1, x2, y2 = (float(value) for value in box)
                detections.append({
                    "class_id": class_index,
                    "label": self.labels[class_index] if class_index < len(self.labels) else str(class_index),
                    "confidence": round(float(score), 6),
                    "xyxy": [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)],
                    "box": [round(x1, 2), round(y1, 2), round(x2 - x1, 2), round(y2 - y1, 2)],
                })
        detections.sort(key=lambda item: item["confidence"], reverse=True)
        max_detections = max(1, min(1_000, int(params.get("max_detections", 100))))
        return {
            "detections": detections[:max_detections],
            "inference_ms": inference_ms,
            "model": str(self.model_path),
            "decoder": decoder,
            "output_shapes": [list(np.asarray(output).shape) for output in outputs],
        }

    def _letterbox(self, image: np.ndarray) -> tuple[np.ndarray, float, tuple[float, float]]:
        height, width = image.shape[:2]
        ratio = min(self.input_width / width, self.input_height / height)
        resized_width = int(round(width * ratio))
        resized_height = int(round(height * ratio))
        resized = self._cv2.resize(image, (resized_width, resized_height), interpolation=self._cv2.INTER_LINEAR)
        dw = (self.input_width - resized_width) / 2
        dh = (self.input_height - resized_height) / 2
        left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
        top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
        result = self._cv2.copyMakeBorder(
            resized,
            top,
            bottom,
            left,
            right,
            self._cv2.BORDER_CONSTANT,
            value=(0, 0, 0),
        )
        return result, ratio, (dw, dh)

    @staticmethod
    def _runtime_input(image: np.ndarray) -> np.ndarray:
        """RKNN Lite requires an explicit batch dimension for this static model."""
        if image.ndim != 3:
            raise ValueError(f"RKNN YOLO input must be HWC before batching, got {image.shape}")
        return np.ascontiguousarray(np.expand_dims(image, axis=0))

    def _post_process(
        self,
        raw_outputs: list[Any],
        confidence: float,
        nms_threshold: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        outputs = [np.asarray(output) for output in raw_outputs]
        if len(outputs) == 1:
            return self._post_process_flat(outputs[0], confidence, nms_threshold)
        if len(outputs) < 6 or len(outputs) % 3 != 0:
            raise ValueError(
                "unsupported RKNN YOLOv8 outputs; expected one flat tensor or "
                "6/9 Model Zoo tensors, "
                f"got {[list(output.shape) for output in outputs]}"
            )
        tensors_per_branch = len(outputs) // 3
        all_boxes: list[np.ndarray] = []
        all_scores: list[np.ndarray] = []
        all_classes: list[np.ndarray] = []
        for branch in range(3):
            position = self._position_nchw(outputs[branch * tensors_per_branch])
            class_scores = self._class_nchw(
                outputs[branch * tensors_per_branch + 1],
                position.shape[2:],
            )
            boxes = self._decode_dfl(position)
            boxes = boxes.transpose(0, 2, 3, 1).reshape(-1, 4)
            probabilities = class_scores.transpose(0, 2, 3, 1).reshape(-1, class_scores.shape[1])
            if os.getenv("MAA_YOLO_SCORES_ARE_LOGITS", "0") == "1":
                probabilities = 1.0 / (1.0 + np.exp(-probabilities))
            classes = np.argmax(probabilities, axis=1)
            scores = probabilities[np.arange(probabilities.shape[0]), classes]
            selected = scores >= confidence
            all_boxes.append(boxes[selected])
            all_scores.append(scores[selected])
            all_classes.append(classes[selected])

        boxes = np.concatenate(all_boxes) if all_boxes else np.empty((0, 4), dtype=np.float32)
        scores = np.concatenate(all_scores) if all_scores else np.empty((0,), dtype=np.float32)
        classes = np.concatenate(all_classes) if all_classes else np.empty((0,), dtype=np.int64)
        if not boxes.size:
            return boxes, classes, scores

        return self._classwise_nms(boxes, classes, scores, nms_threshold)

    def _post_process_flat(
        self,
        output: np.ndarray,
        confidence: float,
        nms_threshold: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Decode standard Ultralytics YOLOv8 [1, 4+classes, candidates] output."""
        array = np.asarray(output)
        if array.ndim == 3:
            if array.shape[0] != 1:
                raise ValueError(f"flat YOLO output batch must be 1, got {array.shape}")
            array = array[0]
        if array.ndim != 2:
            raise ValueError(f"flat YOLO output must be 2D/3D, got {array.shape}")

        # Real exports are usually [84, 8400]; some runtimes return [8400, 84].
        if array.shape[0] < array.shape[1]:
            array = array.T
        if array.shape[1] < 5:
            raise ValueError(f"flat YOLO output has too few features: {array.shape}")

        raw_boxes = array[:, :4].astype(np.float32)
        class_probabilities = array[:, 4:].astype(np.float32)
        if os.getenv("MAA_YOLO_SCORES_ARE_LOGITS", "0") == "1":
            class_probabilities = 1.0 / (1.0 + np.exp(-class_probabilities))
        classes = np.argmax(class_probabilities, axis=1)
        scores = class_probabilities[np.arange(class_probabilities.shape[0]), classes]
        selected = scores >= confidence
        if not np.any(selected):
            return (
                np.empty((0, 4), dtype=np.float32),
                np.empty((0,), dtype=np.int64),
                np.empty((0,), dtype=np.float32),
            )

        xywh = raw_boxes[selected]
        boxes = np.empty_like(xywh, dtype=np.float32)
        boxes[:, 0] = xywh[:, 0] - xywh[:, 2] / 2
        boxes[:, 1] = xywh[:, 1] - xywh[:, 3] / 2
        boxes[:, 2] = xywh[:, 0] + xywh[:, 2] / 2
        boxes[:, 3] = xywh[:, 1] + xywh[:, 3] / 2
        return self._classwise_nms(
            boxes,
            classes[selected],
            scores[selected],
            nms_threshold,
        )

    def _classwise_nms(
        self,
        boxes: np.ndarray,
        classes: np.ndarray,
        scores: np.ndarray,
        threshold: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if not boxes.size:
            return boxes, classes, scores
        kept: list[int] = []
        for class_id in np.unique(classes):
            class_indices = np.flatnonzero(classes == class_id)
            local_kept = self._nms(boxes[class_indices], scores[class_indices], threshold)
            kept.extend(class_indices[local_kept].tolist())
        order = np.asarray(kept, dtype=np.int64)
        order = order[np.argsort(scores[order])[::-1]]
        return boxes[order], classes[order], scores[order]

    def _decode_dfl(self, position: np.ndarray) -> np.ndarray:
        batch, channels, height, width = position.shape
        bins = channels // 4
        distribution = position.reshape(batch, 4, bins, height, width).astype(np.float32)
        distribution -= np.max(distribution, axis=2, keepdims=True)
        distribution = np.exp(distribution)
        distribution /= np.sum(distribution, axis=2, keepdims=True)
        distances = np.sum(
            distribution * np.arange(bins, dtype=np.float32).reshape(1, 1, bins, 1, 1),
            axis=2,
        )
        columns, rows = np.meshgrid(np.arange(width), np.arange(height))
        grid = np.stack((columns, rows), axis=0).reshape(1, 2, height, width) + 0.5
        stride = np.array(
            [self.input_width / width, self.input_height / height], dtype=np.float32
        ).reshape(1, 2, 1, 1)
        top_left = (grid - distances[:, 0:2]) * stride
        bottom_right = (grid + distances[:, 2:4]) * stride
        return np.concatenate((top_left, bottom_right), axis=1)

    @staticmethod
    def _position_nchw(value: np.ndarray) -> np.ndarray:
        array = value if value.ndim == 4 else np.expand_dims(value, axis=0)
        if array.ndim != 4:
            raise ValueError(f"invalid YOLO position tensor: {array.shape}")
        if array.shape[1] % 4 == 0 and array.shape[1] <= 256 and array.shape[2] == array.shape[3]:
            return array
        if array.shape[-1] % 4 == 0 and array.shape[-1] <= 256 and array.shape[1] == array.shape[2]:
            return array.transpose(0, 3, 1, 2)
        raise ValueError(f"cannot determine YOLO position tensor layout: {array.shape}")

    @staticmethod
    def _class_nchw(value: np.ndarray, spatial: tuple[int, int]) -> np.ndarray:
        array = value if value.ndim == 4 else np.expand_dims(value, axis=0)
        if array.ndim != 4:
            raise ValueError(f"invalid YOLO class tensor: {array.shape}")
        if tuple(array.shape[2:]) == tuple(spatial):
            return array
        if tuple(array.shape[1:3]) == tuple(spatial):
            return array.transpose(0, 3, 1, 2)
        raise ValueError(f"cannot determine YOLO class tensor layout: {array.shape}")

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> np.ndarray:
        x1, y1, x2, y2 = boxes.T
        areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
        order = scores.argsort()[::-1]
        keep: list[int] = []
        while order.size:
            index = int(order[0])
            keep.append(index)
            xx1 = np.maximum(x1[index], x1[order[1:]])
            yy1 = np.maximum(y1[index], y1[order[1:]])
            xx2 = np.minimum(x2[index], x2[order[1:]])
            yy2 = np.minimum(y2[index], y2[order[1:]])
            intersection = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
            union = areas[index] + areas[order[1:]] - intersection
            iou = np.divide(intersection, union, out=np.zeros_like(intersection), where=union > 0)
            order = order[np.flatnonzero(iou <= threshold) + 1]
        return np.asarray(keep, dtype=np.int64)

    @staticmethod
    def _restore_boxes(
        boxes: np.ndarray,
        source_shape: tuple[int, int],
        ratio: float,
        padding: tuple[float, float],
    ) -> np.ndarray:
        result = boxes.astype(np.float32, copy=True)
        result[:, [0, 2]] = (result[:, [0, 2]] - padding[0]) / ratio
        result[:, [1, 3]] = (result[:, [1, 3]] - padding[1]) / ratio
        height, width = source_shape
        result[:, [0, 2]] = np.clip(result[:, [0, 2]], 0, width)
        result[:, [1, 3]] = np.clip(result[:, [1, 3]], 0, height)
        return result

    def _allowed_class_ids(self, params: dict[str, Any]) -> set[int] | None:
        raw = params.get("class_ids", params.get("class_id"))
        if raw is not None and raw != "":
            values = raw if isinstance(raw, list) else [raw]
            return {int(value) for value in values}
        names = params.get("class_names", params.get("class_name"))
        if names is None or names == "":
            return None
        values = names if isinstance(names, list) else [names]
        lookup = {label: index for index, label in enumerate(self.labels)}
        missing = [str(value) for value in values if str(value) not in lookup]
        if missing:
            raise ValueError(f"unknown YOLO class names: {', '.join(missing)}")
        return {lookup[str(value)] for value in values}

    @staticmethod
    def _float_param(params: dict[str, Any], *names: str, default: float) -> float:
        value: Any = default
        for name in names:
            if name in params:
                value = params[name]
                break
        result = float(value)
        if not 0 <= result <= 1:
            raise ValueError(f"{names[0]} must be between 0 and 1")
        return result

    @staticmethod
    def _input_size(value: str) -> tuple[int, int]:
        normalized = value.lower().replace("*", "x").replace(",", "x")
        parts = [part.strip() for part in normalized.split("x") if part.strip()]
        if len(parts) == 1:
            parts *= 2
        if len(parts) != 2:
            raise ValueError("MAA_YOLO_INPUT_SIZE must look like 640x640")
        width, height = (int(part) for part in parts)
        if width <= 0 or height <= 0:
            raise ValueError("MAA_YOLO_INPUT_SIZE must be positive")
        return width, height

    @staticmethod
    def _select_model(configured: str) -> Path:
        candidates = [configured] if configured else list(DEFAULT_MODELS)
        for candidate in candidates:
            path = Path(candidate)
            if path.is_file():
                return path
        raise FileNotFoundError(f"RKNN YOLO model was not found: {', '.join(candidates)}")

    @staticmethod
    def _load_labels(configured: str) -> tuple[str, ...]:
        if not configured:
            return COCO_LABELS
        path = Path(configured)
        if not path.is_file():
            raise FileNotFoundError(f"YOLO labels file does not exist: {path}")
        labels = tuple(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        if not labels:
            raise ValueError(f"YOLO labels file is empty: {path}")
        return labels

    def close(self) -> None:
        if self._rknn is not None:
            self._rknn.release()
            self._rknn = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
