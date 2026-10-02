from __future__ import annotations

import itertools
import math
from typing import Any


def find_color_markers(
    image: Any,
    config: dict[str, Any],
    roi: tuple[int, int, int, int] | None = None,
) -> list[dict[str, Any]]:
    """Locate compact groups of three animated color bars and their click points."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("color marker recognition requires OpenCV and NumPy") from exc

    if image is None or not hasattr(image, "shape") or len(image.shape) < 2:
        raise ValueError("color marker recognition received an invalid image")
    image_height, image_width = int(image.shape[0]), int(image.shape[1])
    offset_x = 0
    offset_y = 0
    sample = image
    if roi is not None and roi[2] > 0 and roi[3] > 0:
        x, y, width, height = roi
        x = max(0, min(int(x), image_width - 1))
        y = max(0, min(int(y), image_height - 1))
        width = max(1, min(int(width), image_width - x))
        height = max(1, min(int(height), image_height - y))
        offset_x, offset_y = x, y
        sample = image[y : y + height, x : x + width]

    lower = np.asarray(config.get("hsv_lower", [10, 160, 200]), dtype=np.uint8)
    upper = np.asarray(config.get("hsv_upper", [40, 255, 255]), dtype=np.uint8)
    hsv = cv2.cvtColor(sample, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, lower, upper)
    mask = cv2.morphologyEx(
        mask,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
    )
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(
        mask,
        connectivity=8,
    )
    min_area = int(config.get("component_min_area", 250))
    max_area = int(config.get("component_max_area", 1800))
    components: list[dict[str, Any]] = []
    for component_id in range(1, count):
        x, y, width, height, area = [int(value) for value in stats[component_id]]
        if not min_area <= area <= max_area:
            continue
        if width < 18 or height < 8 or width > 110 or height > 75:
            continue
        if area / max(1, width * height) < 0.3:
            continue
        components.append(
            {
                "id": component_id,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "area": area,
                "cx": float(centroids[component_id][0]),
                "cy": float(centroids[component_id][1]),
            }
        )

    # Avoid an accidental combinatorial explosion on yellow-heavy screens.
    components = sorted(components, key=lambda item: item["area"], reverse=True)[:48]
    group_distance = float(config.get("group_distance", 115))
    candidates: list[dict[str, Any]] = []
    for group in itertools.combinations(components, 3):
        distances = [
            math.hypot(left["cx"] - right["cx"], left["cy"] - right["cy"])
            for left, right in itertools.combinations(group, 2)
        ]
        if max(distances) > group_distance:
            continue
        left = min(item["x"] for item in group)
        top = min(item["y"] for item in group)
        right = max(item["x"] + item["width"] for item in group)
        bottom = max(item["y"] + item["height"] for item in group)
        width = right - left
        height = bottom - top
        if not 35 <= width <= 185 or not 55 <= height <= 205:
            continue
        if max(item["cy"] for item in group) - min(item["cy"] for item in group) < 28:
            continue
        area = sum(item["area"] for item in group)
        density = area / max(1, width * height)
        if density < 0.12:
            continue
        candidates.append(
            {
                "component_ids": [item["id"] for item in group],
                "box": [left + offset_x, top + offset_y, width, height],
                "score": round(area * density / (1.0 + max(distances) / group_distance), 4),
            }
        )

    # One marker can yield several overlapping triples if antialiasing creates a
    # fourth fragment. Keep the strongest non-overlapping group only.
    selected: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: item["score"], reverse=True):
        ids = set(candidate["component_ids"])
        if any(len(ids.intersection(existing["component_ids"])) >= 2 for existing in selected):
            continue
        selected.append(candidate)

    anchor = str(config.get("anchor") or "center")
    click_offset_x = int(config.get("offset_x", 0))
    click_offset_y = int(config.get("offset_y", 0))
    for candidate in selected:
        x, y, width, height = candidate["box"]
        anchors = {
            "top_left": (x, y),
            "top_right": (x + width - 1, y),
            "center": (x + (width - 1) // 2, y + (height - 1) // 2),
            "bottom_left": (x, y + height - 1),
            "bottom_right": (x + width - 1, y + height - 1),
        }
        target_x, target_y = anchors.get(anchor, anchors["center"])
        target_x = max(0, min(image_width - 1, target_x + click_offset_x))
        target_y = max(0, min(image_height - 1, target_y + click_offset_y))
        candidate["target"] = [target_x, target_y]

    order = str(config.get("order_by") or "Vertical")
    if order == "Horizontal":
        selected.sort(key=lambda item: (item["target"][0], item["target"][1]))
    elif order == "Score":
        selected.sort(key=lambda item: item["score"], reverse=True)
    else:
        selected.sort(key=lambda item: (item["target"][1], item["target"][0]))
    return selected
