from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

DetectionArrays = tuple[np.ndarray, np.ndarray, np.ndarray]


class YoloV8PostProcessor:
    """Decode the RKNN Model Zoo and flat Ultralytics YOLOv8 layouts."""

    def __init__(
        self,
        *,
        input_width: int,
        input_height: int,
        scores_are_logits: bool = False,
    ) -> None:
        if input_width < 1 or input_height < 1:
            raise ValueError("YOLO input dimensions must be positive")
        self.input_width = input_width
        self.input_height = input_height
        self.scores_are_logits = scores_are_logits

    def decode(
        self,
        raw_outputs: Sequence[Any],
        *,
        confidence: float,
        nms_threshold: float,
    ) -> DetectionArrays:
        outputs = [np.asarray(output) for output in raw_outputs]
        if len(outputs) == 1:
            return self._decode_flat(outputs[0], confidence, nms_threshold)
        if len(outputs) not in {6, 9}:
            shapes = [list(output.shape) for output in outputs]
            raise ValueError(
                "unsupported RKNN YOLOv8 outputs; expected one flat tensor or "
                f"6/9 Model Zoo tensors, got {shapes}"
            )
        return self._decode_model_zoo(outputs, confidence, nms_threshold)

    def _decode_model_zoo(
        self,
        outputs: list[np.ndarray],
        confidence: float,
        nms_threshold: float,
    ) -> DetectionArrays:
        tensors_per_branch = len(outputs) // 3
        boxes_by_branch: list[np.ndarray] = []
        scores_by_branch: list[np.ndarray] = []
        classes_by_branch: list[np.ndarray] = []
        for branch in range(3):
            position = self.position_nchw(outputs[branch * tensors_per_branch])
            class_scores = self.class_nchw(
                outputs[branch * tensors_per_branch + 1],
                position.shape[2:],
            )
            boxes, classes, scores = self._decode_branch(position, class_scores, confidence)
            boxes_by_branch.append(boxes)
            classes_by_branch.append(classes)
            scores_by_branch.append(scores)

        boxes = np.concatenate(boxes_by_branch)
        classes = np.concatenate(classes_by_branch)
        scores = np.concatenate(scores_by_branch)
        return self.classwise_nms(boxes, classes, scores, nms_threshold)

    def _decode_branch(
        self,
        position: np.ndarray,
        class_scores: np.ndarray,
        confidence: float,
    ) -> DetectionArrays:
        boxes = self._decode_dfl(position).transpose(0, 2, 3, 1).reshape(-1, 4)
        probabilities = class_scores.transpose(0, 2, 3, 1).reshape(-1, class_scores.shape[1])
        probabilities = self._probabilities(probabilities)
        classes = np.argmax(probabilities, axis=1)
        scores = probabilities[np.arange(probabilities.shape[0]), classes]
        selected = scores >= confidence
        return boxes[selected], classes[selected], scores[selected]

    def _decode_flat(
        self,
        output: np.ndarray,
        confidence: float,
        nms_threshold: float,
    ) -> DetectionArrays:
        array = np.asarray(output)
        if array.ndim == 3:
            if array.shape[0] != 1:
                raise ValueError(f"flat YOLO output batch must be 1, got {array.shape}")
            array = array[0]
        if array.ndim != 2:
            raise ValueError(f"flat YOLO output must be 2D/3D, got {array.shape}")
        if array.shape[0] < array.shape[1]:
            array = array.T
        if array.shape[1] < 5:
            raise ValueError(f"flat YOLO output has too few features: {array.shape}")

        raw_boxes = array[:, :4].astype(np.float32)
        probabilities = self._probabilities(array[:, 4:].astype(np.float32))
        classes = np.argmax(probabilities, axis=1)
        scores = probabilities[np.arange(probabilities.shape[0]), classes]
        selected = scores >= confidence
        if not np.any(selected):
            return self.empty()
        boxes = self._xywh_to_xyxy(raw_boxes[selected])
        return self.classwise_nms(boxes, classes[selected], scores[selected], nms_threshold)

    def _probabilities(self, values: np.ndarray) -> np.ndarray:
        if not self.scores_are_logits:
            return values
        return 1.0 / (1.0 + np.exp(-values))

    def _decode_dfl(self, position: np.ndarray) -> np.ndarray:
        batch, channels, height, width = position.shape
        if channels % 4 != 0:
            raise ValueError(f"YOLO DFL channels must be divisible by 4, got {channels}")
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
            [self.input_width / width, self.input_height / height],
            dtype=np.float32,
        ).reshape(1, 2, 1, 1)
        return np.concatenate(
            ((grid - distances[:, 0:2]) * stride, (grid + distances[:, 2:4]) * stride),
            axis=1,
        )

    @staticmethod
    def position_nchw(value: np.ndarray) -> np.ndarray:
        array = value if value.ndim == 4 else np.expand_dims(value, axis=0)
        if array.ndim != 4:
            raise ValueError(f"invalid YOLO position tensor: {array.shape}")
        if array.shape[1] % 4 == 0 and array.shape[1] <= 256 and array.shape[2] == array.shape[3]:
            return array
        if array.shape[-1] % 4 == 0 and array.shape[-1] <= 256 and array.shape[1] == array.shape[2]:
            return array.transpose(0, 3, 1, 2)
        raise ValueError(f"cannot determine YOLO position tensor layout: {array.shape}")

    @staticmethod
    def class_nchw(value: np.ndarray, spatial: tuple[int, int]) -> np.ndarray:
        array = value if value.ndim == 4 else np.expand_dims(value, axis=0)
        if array.ndim != 4:
            raise ValueError(f"invalid YOLO class tensor: {array.shape}")
        if tuple(array.shape[2:]) == tuple(spatial):
            return array
        if tuple(array.shape[1:3]) == tuple(spatial):
            return array.transpose(0, 3, 1, 2)
        raise ValueError(f"cannot determine YOLO class tensor layout: {array.shape}")

    @classmethod
    def classwise_nms(
        cls,
        boxes: np.ndarray,
        classes: np.ndarray,
        scores: np.ndarray,
        threshold: float,
    ) -> DetectionArrays:
        if not boxes.size:
            return boxes, classes, scores
        kept: list[int] = []
        for class_id in np.unique(classes):
            class_indices = np.flatnonzero(classes == class_id)
            local_kept = cls._nms(boxes[class_indices], scores[class_indices], threshold)
            kept.extend(class_indices[local_kept].tolist())
        order = np.asarray(kept, dtype=np.int64)
        order = order[np.argsort(scores[order])[::-1]]
        return boxes[order], classes[order], scores[order]

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
    def restore_boxes(
        boxes: np.ndarray,
        *,
        source_shape: tuple[int, int],
        ratio: float,
        padding: tuple[float, float],
    ) -> np.ndarray:
        if ratio <= 0:
            raise ValueError("letterbox ratio must be positive")
        result = boxes.astype(np.float32, copy=True)
        result[:, [0, 2]] = (result[:, [0, 2]] - padding[0]) / ratio
        result[:, [1, 3]] = (result[:, [1, 3]] - padding[1]) / ratio
        height, width = source_shape
        result[:, [0, 2]] = np.clip(result[:, [0, 2]], 0, width)
        result[:, [1, 3]] = np.clip(result[:, [1, 3]], 0, height)
        return result

    @staticmethod
    def _xywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
        result = np.empty_like(boxes, dtype=np.float32)
        result[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
        result[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
        result[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
        result[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
        return result

    @staticmethod
    def empty() -> DetectionArrays:
        return (
            np.empty((0, 4), dtype=np.float32),
            np.empty((0,), dtype=np.int64),
            np.empty((0,), dtype=np.float32),
        )
