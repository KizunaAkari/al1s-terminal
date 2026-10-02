"""Locate report sword/shield icons; never derive attack from left or from Win."""

from pathlib import Path

import cv2
import numpy as np


class RoleMatcher:
    def __init__(self, assets: Path):
        self.templates = {}
        for role in ("sword", "shield"):
            source = cv2.imread(str(assets / "roles" / f"{role}.png"))
            if source is None:
                raise ValueError("lineup_role_assets_missing")
            h, w = source.shape[:2]
            self.templates[role] = [
                cv2.Canny(cv2.resize(source, (round(w * size / h), size)), 80, 180)
                for size in range(36, 100, 2)
            ]

    def attack_side(self, image: np.ndarray) -> str | None:
        h, w = image.shape[:2]
        if min(h, w) == 0 or h > w:
            return None
        image = cv2.resize(image, (1600, round(h * 1600 / w)))
        h, w = image.shape[:2]
        roles = []
        for side in range(2):
            region = image[
                int(h * 0.10) : int(h * 0.42),
                int(w * side * 0.5) : int(w * (side * 0.5 + 0.2)),
            ]
            scores = sorted(
                (
                    (self._score(region, variants), role)
                    for role, variants in self.templates.items()
                ),
                reverse=True,
            )
            if scores[0][0] < 0.50 or scores[0][0] - scores[1][0] < 0.15:
                return None
            roles.append(scores[0][1])
        if roles == ["sword", "shield"]:
            return "left"
        if roles == ["shield", "sword"]:
            return "right"
        return None

    @staticmethod
    def _score(region: np.ndarray, variants: list[np.ndarray]) -> float:
        edges = cv2.Canny(region, 80, 180)
        scores = [
            cv2.minMaxLoc(cv2.matchTemplate(edges, template, cv2.TM_CCOEFF_NORMED))[1]
            for template in variants
            if template.shape[0] <= edges.shape[0] and template.shape[1] <= edges.shape[1]
        ]
        return max(scores, default=0.0)
