"""Per-process immutable models; per-image matches never survive a request."""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from functools import cached_property
from pathlib import Path
from typing import Any

from al1s_terminal.lineup.detector import PortraitDetector
from al1s_terminal.lineup.matching import PortraitMatcher
from al1s_terminal.lineup.ocr import NameOcr
from al1s_terminal.lineup.roles import RoleMatcher


class LineupResources:
    def __init__(self, assets: Path, ocr: Path):
        self.assets, self.ocr_path = assets, ocr

    @cached_property
    def catalog(self) -> dict[str, Any]:
        value = json.loads((self.assets / "catalog.json").read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("lineup_catalog_invalid")
        return value

    @cached_property
    def ocr(self) -> NameOcr:
        return NameOcr(self.ocr_path, self.assets / "ocr-fallback")

    @cached_property
    def detector(self) -> PortraitDetector:
        return PortraitDetector(self.assets)

    @cached_property
    def matcher(self) -> PortraitMatcher:
        return PortraitMatcher(self.assets, self.catalog["students"])

    @cached_property
    def roles(self) -> RoleMatcher:
        return RoleMatcher(self.assets)


def match_portraits(
    image: Any,
    teams: dict[str, list[dict[str, Any]]],
    resources: LineupResources,
) -> dict[tuple[str, int], tuple[int, float, float]]:
    jobs = [
        (side, index, region["box"])
        for side, row in teams.items()
        for index, region in enumerate(row)
        if region["kind"] == "portrait"
    ]
    if not jobs:
        return {}
    matcher = resources.matcher  # Load once before entering threads.

    def match(job: tuple[str, int, list[int]]) -> tuple[int, float, float]:
        x, y, w, h = job[2]
        return matcher.match(image[y : y + h, x : x + w])

    with ThreadPoolExecutor(max_workers=min(8, len(jobs), os.cpu_count() or 1)) as pool:
        matches = list(pool.map(match, jobs))
    return {(side, index): result for (side, index, _), result in zip(jobs, matches, strict=True)}
