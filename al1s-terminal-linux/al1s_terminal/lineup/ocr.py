"""Image-only OCR; name resolution never receives a portrait candidate."""

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from al1s_terminal.lineup.matching import name_id


class ImageOcr:
    def __init__(self, path: Path):
        from maa.controller import CustomController
        from maa.resource import Resource
        from maa.tasker import Tasker

        class ImageController(CustomController):  # type: ignore[misc]
            def connect(self) -> bool:
                return True

            def request_uuid(self) -> str:
                return "lineup-image-only"

        self.resource, self.controller, self.tasker = Resource(), ImageController(), Tasker()
        if not self.resource.post_ocr_model(path).wait().succeeded:
            raise RuntimeError("lineup_ocr_unavailable")
        self.controller.post_connection().wait()
        if not self.tasker.bind(self.resource, self.controller):
            raise RuntimeError("lineup_ocr_unavailable")

    def read(self, image: Any) -> tuple[str, float]:
        from maa.pipeline import JOCR, JRecognitionType

        image = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        detail = (
            self.tasker.post_recognition(
                JRecognitionType.OCR, JOCR(expected=[], order_by="Vertical"), image
            )
            .wait()
            .get()
        )
        entries = []
        if detail:
            for node in detail.nodes:
                if node.recognition:
                    for item in node.recognition.all_results:
                        if getattr(item, "text", ""):
                            entries.append((str(item.text)[:100], float(getattr(item, "score", 0))))
        return "".join(t for t, _ in entries)[:200], min((s for _, s in entries), default=0.0)

    def scan(self, image: Any) -> list[dict[str, Any]]:
        from maa.pipeline import JOCR, JRecognitionType

        h, w = image.shape[:2]
        scale = min(2.0, 2400 / max(h, w))
        sample = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        detail = (
            self.tasker.post_recognition(
                JRecognitionType.OCR, JOCR(expected=[], order_by="Vertical"), sample
            )
            .wait()
            .get()
        )
        entries = []
        if detail:
            for node in detail.nodes:
                if not node.recognition:
                    continue
                for item in node.recognition.all_results[:100]:
                    if not getattr(item, "text", ""):
                        continue
                    rect = item.box
                    coordinates = (
                        rect
                        if isinstance(rect, (list, tuple))
                        else (rect.x, rect.y, rect.w, rect.h)
                    )
                    x, y, rw, rh = [round(value / scale) for value in coordinates]
                    x1, y1 = max(0, x), max(0, y)
                    box = [x1, y1, min(w, x + rw) - x1, min(h, y + rh) - y1]
                    if min(box[2:]) <= 0:
                        continue
                    entries.append(
                        dict(box=box, text=str(item.text)[:100], confidence=float(item.score))
                    )
        return entries

    def read_name(
        self, image: Any, students: list[dict[str, Any]], min_confidence: float = 0.75
    ) -> tuple[str, float, int | None]:
        first = self.read(image)
        first_id = name_id(first[0], students)
        if first_id is not None and first[1] >= min_confidence:
            return *first, first_id
        # A short, deterministic retry for low-confidence or unmapped text.
        # Independent OCR alternatives that identify different students conflict.
        observations = [(first[0], first[1], first_id)]
        h, w = image.shape[:2]
        for shear in (0.15, 0.25):
            corrected = cv2.warpAffine(
                image,
                np.array([[1, shear, 0], [0, 1, 0]], dtype=np.float32),
                (w + round(h * shear), h),
                borderMode=cv2.BORDER_REPLICATE,
            )
            padded = cv2.copyMakeBorder(corrected, 5, 5, 5, 5, cv2.BORDER_REPLICATE)
            text, score = self.read(padded)
            observations.append((text, score, name_id(text, students)))
        return resolve_observations(observations)


def resolve_observations(
    observations: list[tuple[str, float, int | None]],
) -> tuple[str, float, int | None]:
    mapped = [item for item in observations if item[2] is not None]
    if len({item[2] for item in mapped}) == 1:
        return max(mapped, key=lambda item: item[1])
    text, score, _ = max(observations, key=lambda item: item[1])
    return text, score, None


class NameOcr:
    """Load the larger recognizer only for unresolved mobile OCR observations."""

    def __init__(self, primary_path: Path, fallback_path: Path):
        self.primary = ImageOcr(primary_path)
        self.fallback_path = fallback_path
        self.fallback: ImageOcr | None = None

    def read_name(
        self, image: Any, students: list[dict[str, Any]], min_confidence: float = 0.75
    ) -> tuple[str, float, int | None]:
        first = self.primary.read_name(image, students, min_confidence)
        if first[2] is not None and first[1] >= min_confidence:
            return first
        if self.fallback is None:
            self.fallback = ImageOcr(self.fallback_path)
        second = self.fallback.read_name(image, students, min_confidence)
        return resolve_observations([first, second])
