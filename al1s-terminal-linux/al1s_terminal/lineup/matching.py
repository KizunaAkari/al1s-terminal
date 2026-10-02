"""Independent portrait and literal-name matching with conservative agreement gates."""

import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from al1s_terminal.lineup.identity import normalized_name


def name_id(text: str, students: list[dict[str, Any]]) -> int | None:
    key = normalized_name(text)
    ids = {
        s["id"]
        for s in students
        if key and any(normalized_name(alias) == key for alias in s["aliases"])
    }
    return next(iter(ids)) if len(ids) == 1 else None


class PortraitMatcher:
    def __init__(self, assets: Path, students: list[dict[str, Any]]):
        self.ids = [s["id"] for s in students]
        self.templates = []
        for sid in self.ids:
            image = cv2.imread(str(assets / "portraits" / f"{sid}.webp"), cv2.IMREAD_UNCHANGED)
            if image is None:
                raise ValueError("lineup_portrait_missing")
            image = cv2.resize(image, (64, 64))
            fine = image[8:56, 12:52]
            mask = fine[:, :, 3] if image.shape[2] == 4 else None
            self.templates.append(
                [
                    (
                        cv2.GaussianBlur(image[12:52, 16:48, :3], (3, 3), 0),
                        cv2.GaussianBlur(fine[:, :, :3], (3, 3), 0),
                        mask,
                    )
                ]
            )
        self._load_samples(assets)

    def _load_samples(self, assets: Path) -> None:
        manifest = assets / "portrait-samples.json"
        if manifest.is_file():
            entries = json.loads(manifest.read_text(encoding="utf-8"))["samples"]
            if len(entries) > len(self.ids) * 8:
                raise ValueError("lineup_samples_invalid")
            for entry in entries:
                index = self.ids.index(entry["student_id"])
                path = (assets / entry["path"]).resolve()
                if not path.is_relative_to((assets / "portrait-samples").resolve()):
                    raise ValueError("lineup_samples_invalid")
                if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                    raise ValueError("lineup_sample_hash_mismatch")
                image = cv2.imread(str(path))
                if image is None or len(self.templates[index]) > 8:
                    raise ValueError("lineup_samples_invalid")
                image = cv2.resize(image, (64, 64))
                self.templates[index].append(
                    (
                        cv2.GaussianBlur(image[12:52, 16:48], (3, 3), 0),
                        cv2.GaussianBlur(image[8:56, 12:52], (3, 3), 0),
                        None,
                    )
                )

    def match(self, crop: np.ndarray) -> tuple[int, float, float]:
        samples = [cv2.resize(crop, (scale, scale)) for scale in (56, 64, 72, 80)]
        scores = np.array(
            [
                max(self._score(q, fast) for q in samples for fast, _, _ in templates)
                for templates in self.templates
            ]
        )
        candidates = np.argsort(scores)[-8:]
        refined = np.full(len(self.ids), -1.0, dtype=np.float32)
        for index in candidates:
            refined[index] = max(
                self._score(q, template, mask)
                for q in samples
                for _, template, mask in self.templates[index]
            )
        initial = self._result(refined)
        form_gate = initial[0] not in (10098, 10099) or (initial[1] >= 0.90 and initial[2] >= 0.15)
        if initial[1] >= 0.80 and initial[2] >= 0.10 and form_gate:
            return initial
        # Report portrait frames are wider than they are tall. Independently fit
        # both axes instead of stretching every detection to a square.
        coarse = {
            (w, h): cv2.resize(crop, (w, h)) for w in range(48, 89, 8) for h in range(48, 89, 8)
        }
        for index in candidates:
            _, template, mask = max(
                self.templates[index],
                key=lambda item: max(self._score(q, item[1], item[2]) for q in samples),
            )
            ranked = [(self._score(q, template, mask), size) for size, q in coarse.items()]
            best, (w, h) = max(ranked)
            for dx in (-4, 0, 4):
                for dy in (-4, 0, 4):
                    if w + dx >= 44 and h + dy >= 48:
                        sample = cv2.resize(crop, (w + dx, h + dy))
                        best = max(best, self._score(sample, template, mask))
            refined[index] = best
        return self._result(refined)

    def _result(self, scores: np.ndarray) -> tuple[int, float, float]:
        order = np.argsort(scores)[::-1]
        top, second = int(order[0]), int(order[1])
        return (
            self.ids[top],
            round(float(scores[top]), 4),
            round(float(scores[top] - scores[second]), 4),
        )

    @staticmethod
    def _score(sample: Any, template: Any, mask: Any = None) -> float:
        values = cv2.matchTemplate(sample, template, cv2.TM_CCOEFF_NORMED, mask=mask)
        finite = values[np.isfinite(values)]
        return float(finite.max()) if finite.size else -1.0


def agreed(
    image_id: int | None,
    ocr_id: int | None,
    score: float,
    margin: float,
    ocr_score: float,
    layout_valid: bool,
) -> bool:
    return bool(
        layout_valid
        and image_id is not None
        and image_id == ocr_id
        and score >= 0.72
        and margin >= 0.08
        and ocr_score >= 0.75
    )
