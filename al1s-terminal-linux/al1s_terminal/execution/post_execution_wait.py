"""Apply a recognition step's final wait before assertions and successor routing."""

from __future__ import annotations

import math
from typing import Any

RECOGNITION_ACTIONS = frozenset(
    {"recognize_execute", "wait_click", "wait_image", "wait_text", "click_text", "smart_swipe"}
)
SYSTEM_KEY_ACTIONS = frozenset({"back", "home", "task_view"})


def apply_pre_execution_wait(
    pipeline: dict[str, dict[str, Any]], candidates: list[str], step: dict[str, Any]
) -> None:
    seconds = step.get("wait_before_execution_seconds")
    if seconds is None:
        return
    if (
        step.get("action") not in SYSTEM_KEY_ACTIONS
        or isinstance(seconds, bool)
        or not isinstance(seconds, (int, float))
        or not math.isfinite(seconds)
        or not 0 <= seconds <= 14400
    ):
        raise ValueError("wait before execution must be between 0 and 14400 on system keys")
    for name in candidates:
        node = pipeline[name]
        node["pre_delay"] = int(node.get("pre_delay", 0)) + round(seconds * 1000)


def apply_post_execution_wait(
    pipeline: dict[str, dict[str, Any]], exits: list[str], step: dict[str, Any]
) -> None:
    seconds = step.get("wait_after_execution_seconds")
    if seconds is None:
        return
    if (
        step.get("action") not in RECOGNITION_ACTIONS | SYSTEM_KEY_ACTIONS
        or isinstance(seconds, bool)
        or not isinstance(seconds, (int, float))
        or not math.isfinite(seconds)
        or not 0 <= seconds <= 14400
    ):
        raise ValueError(
            "wait after execution must be between 0 and 14400 seconds on recognition steps"
        )
    if seconds == 0:
        return
    for name in exits:
        node = pipeline[name]
        node["post_delay"] = int(node.get("post_delay", 0)) + round(seconds * 1000)
