"""Bounded local lineup inference with explicit evidence and absent sides."""

import argparse
import json
import time
from pathlib import Path
from typing import Any

import cv2

from al1s_terminal.lineup.decision import decision
from al1s_terminal.lineup.executor import MODEL_VERSION
from al1s_terminal.lineup.identity import ambiguous_ids
from al1s_terminal.lineup.layout import assign_teams, horizontal_row
from al1s_terminal.lineup.matching import name_id
from al1s_terminal.lineup.ocr import NameOcr, resolve_observations
from al1s_terminal.lineup.regions import (
    merge_wrapped_names,
    padded_region,
    portrait_row,
    reflow_wrapped_name,
    split_joined_labels,
)
from al1s_terminal.lineup.resources import LineupResources, match_portraits


def locate(image: Any, resources: LineupResources, hint: str, mode: str) -> list[dict[str, Any]]:
    single = hint in ("attack", "defense")
    row = [] if mode == "text" else portrait_row(image, resources.detector, single)
    if not row and mode != "portrait":
        labels = split_joined_labels(
            image, resources.ocr.primary.scan(image), resources.ocr.primary
        )
        row = horizontal_row(merge_wrapped_names(labels), 6 if single else None, partial=True)
    return row


def read_text_region(
    image: Any, region: dict[str, Any], ocr: NameOcr, students: list[dict[str, Any]]
) -> tuple[str, float, int | None]:
    observations = [(region["text"], region["confidence"], name_id(region["text"], students))]
    read = ocr.read_name(padded_region(image, region["box"]), students, min_confidence=0.90)
    observations.append(read)
    if read[1] < 0.90 or read[2] is None:
        reflowed = reflow_wrapped_name(image, region.get("parts", []))
        if reflowed is not None:
            observations.append(ocr.read_name(reflowed, students, min_confidence=0.90))
    return resolve_observations(observations)


def recognize_slot(
    image: Any,
    region: dict[str, Any] | None,
    side: str,
    index: int,
    mode: str,
    portrait: tuple[int, float, float] | None,
    ocr: NameOcr,
    students: list[dict[str, Any]],
) -> dict[str, Any]:
    slot: dict[str, Any] = dict(
        side=side,
        index=index,
        present=region is not None,
        box=None,
        ocr_text="",
        image_id=None,
        ocr_id=None,
        ocr_score=0.0,
        score=0.0,
        margin=0.0,
    )
    if region:
        x, y, w, h = region["box"]
        slot["box"] = region["box"]
        if region["kind"] == "portrait":
            assert portrait is not None
            slot["image_id"], slot["score"], slot["margin"] = portrait
            if mode != "portrait":
                x1, x2 = max(0, int(x - w * 0.4)), min(image.shape[1], int(x + w * 1.4))
                y1, y2 = y + h, min(image.shape[0], int(y + h * 1.82))
                if y2 - y1 >= max(8, h * 0.15):
                    crop = image[y1:y2, x1:x2]
                    if crop.std() > 4:
                        slot["ocr_text"], slot["ocr_score"], slot["ocr_id"] = ocr.read_name(
                            crop, students
                        )
        else:
            slot["ocr_text"], slot["ocr_score"], slot["ocr_id"] = read_text_region(
                image, region, ocr, students
            )
    slot["ambiguous_ids"] = ambiguous_ids(slot["image_id"], slot["ocr_text"], students)
    slot["ocr_score"] = round(slot["ocr_score"], 4)
    return {**slot, **decision(slot, True)}


def recognize(
    image_path: Path,
    assets: Path,
    ocr_path: Path,
    layout_hint: str = "auto",
    recognition_mode: str = "auto",
    *,
    resources: LineupResources | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    cv2.setNumThreads(2)
    image = cv2.imread(str(image_path))
    if image is None or image.shape[0] * image.shape[1] > 16_000_000:
        raise ValueError("lineup_image_invalid")
    resources = resources or LineupResources(assets, ocr_path)
    catalog = resources.catalog
    students = catalog["students"]
    ocr = resources.ocr
    row = locate(image, resources, layout_hint, recognition_mode)
    attack_side = resources.roles.attack_side(image) if layout_hint == "auto" else None
    teams = assign_teams(row, layout_hint, attack_side)
    if layout_hint in ("left_attack", "right_attack"):
        attack_side = "left" if layout_hint == "left_attack" else "right"
    portraits = match_portraits(image, teams, resources)
    slots = [
        recognize_slot(
            image,
            teams[side][i] if i < len(teams.get(side, [])) else None,
            side,
            i,
            recognition_mode,
            portraits.get((side, i)),
            ocr,
            students,
        )
        for side in ("attack", "defense")
        for i in range(6)
    ]
    return dict(
        slots=slots,
        teams=[side for side in ("attack", "defense") if side in teams],
        team_sizes={side: len(items) for side, items in teams.items()},
        layout_valid=bool(teams),
        model_version=MODEL_VERSION,
        catalog_version=catalog["version"],
        layout_hint=layout_hint,
        recognition_mode=recognition_mode,
        attack_side=attack_side,
        elapsed_ms=round((time.monotonic() - started) * 1000),
        detector_backend=("not-used" if recognition_mode == "text" else resources.detector.backend),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("image", "assets", "ocr", "output"):
        parser.add_argument(name, type=Path)
    parser.add_argument(
        "--layout-hint",
        default="auto",
        choices=("auto", "attack", "defense", "left_attack", "right_attack"),
    )
    parser.add_argument("--recognition-mode", default="auto", choices=("auto", "portrait", "text"))
    args = parser.parse_args()
    result = recognize(args.image, args.assets, args.ocr, args.layout_hint, args.recognition_mode)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )
