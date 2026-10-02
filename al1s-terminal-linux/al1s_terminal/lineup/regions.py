"""Locate actual portrait or name cells without fabricating missing slots."""

import unicodedata
from itertools import pairwise
from typing import Any

import cv2
import numpy as np

from al1s_terminal.lineup.layout import horizontal_row


def portrait_row(image: Any, detector: Any, single: bool) -> list[dict[str, Any]]:
    h, w = image.shape[:2]
    boxes = detector.boxes(image)
    items = [dict(box=b, kind="portrait") for b in boxes if 0.45 < b[2] / b[3] < 1.8]
    row = horizontal_row(items, 6 if single else None, partial=True)
    if row and (w <= h * 3 or len(row) == (6 if single else 12)):
        return row
    # Tight strips differ from the report layouts seen by the detector. Restore
    # report-like spacing for one extra pass, then map detections to source pixels.
    if w > h * 3:
        canvas = np.full((round(w * 0.65), round(w * 1.2), 3), 245, np.uint8)
        dx, dy = round(w * 0.1), round(w * 0.65) - h - round(w * 0.04)
        canvas[dy : dy + h, dx : dx + w] = image
        mapped = []
        for x, y, bw, bh in detector.boxes(canvas):
            x1, y1, x2, y2 = (
                max(0, x - dx),
                max(0, y - dy),
                min(w, x + bw - dx),
                min(h, y + bh - dy),
            )
            if x2 > x1 and y2 > y1:
                mapped.append(dict(box=[x1, y1, x2 - x1, y2 - y1], kind="portrait"))
        padded = horizontal_row(mapped, 6 if single else None, partial=True)
        return padded if len(padded) >= len(row) else row
    return row


def merge_wrapped_names(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    remaining = sorted(
        [item for item in items if any(c.isalnum() for c in item["text"])],
        key=lambda item: (item["box"][1], item["box"][0]),
    )
    merged = []
    while remaining:
        item = dict(remaining.pop(0))
        x, y, w, h = item["box"]
        normalized = unicodedata.normalize("NFKC", item["text"])
        if "(" in normalized and ")" not in normalized:
            match = next(
                (
                    other
                    for other in remaining
                    if (
                        -h * 0.35 <= other["box"][1] - y - h < h * 1.5
                        and other["box"][1] > y + h * 0.5
                        and abs(other["box"][0] + other["box"][2] / 2 - x - w / 2) < w * 0.7
                    )
                ),
                None,
            )
            if match:
                remaining.remove(match)
                mx, my, mw, mh = match["box"]
                item.update(
                    text=item["text"] + match["text"],
                    parts=[item["box"], match["box"]],
                    box=[min(x, mx), y, max(x + w, mx + mw) - min(x, mx), my + mh - y],
                    confidence=min(item["confidence"], match["confidence"]),
                )
        merged.append({**item, "kind": "text"})
    return merged


def split_joined_labels(image: Any, items: list[dict[str, Any]], ocr: Any) -> list[dict[str, Any]]:
    result = []
    for item in items:
        x, y, w, h = item["box"]
        if w < h * 5:
            result.append(item)
            continue
        gray = cv2.cvtColor(image[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY)
        ink = np.where(np.any(gray < min(180, np.percentile(gray, 85) - 45), axis=0))[0]
        cuts = [int((a + b) / 2) for a, b in pairwise(ink) if b - a > max(6, h * 0.25)]
        if not cuts or len(cuts) > 11:
            result.append(item)
            continue
        edges = [0, *cuts, w]
        for left, right in pairwise(edges):
            box = [x + left, y, right - left, h]
            text, confidence = ocr.read(padded_region(image, box))
            if text:
                result.append(dict(box=box, text=text, confidence=confidence))
    return result


def padded_region(image: Any, box: list[int], padding: int = 3) -> Any:
    x, y, w, h = box
    return image[
        max(0, y - padding) : min(image.shape[0], y + h + padding),
        max(0, x - padding) : min(image.shape[1], x + w + padding),
    ]


def reflow_wrapped_name(image: Any, parts: list[list[int]]) -> Any | None:
    """Read the same two printed lines as one; no catalog text is rendered."""
    if len(parts) != 2:
        return None
    crops = []
    previous_bottom = 0
    for x, y, w, h in parts:
        top = max(y, previous_bottom)
        crop = image[top : y + h, x : x + w]
        previous_bottom = y + h
        if crop.size == 0:
            return None
        ink_y, ink_x = np.where(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) < 160)
        if not len(ink_y):
            return None
        crop = crop[ink_y.min() : ink_y.max() + 1, ink_x.min() : ink_x.max() + 1]
        width = round(crop.shape[1] * 32 / crop.shape[0])
        if not 1 <= width <= 1000:
            return None
        crops.append(cv2.resize(crop, (width, 32), interpolation=cv2.INTER_CUBIC))
    canvas = np.full((44, sum(c.shape[1] + 4 for c in crops) + 12, 3), 250, np.uint8)
    x = 6
    for crop in crops:
        canvas[6:38, x : x + crop.shape[1]] = crop
        x += crop.shape[1] + 4
    return canvas
