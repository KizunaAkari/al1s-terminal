"""Native-size guard using the frame already supplied by Maa recognition."""

from __future__ import annotations

import copy
import json
import time
from typing import Any

GUARD = "Al1sScreenSizeGuard"
READY = "Al1sScreenOrientationReady"


def requires_guard(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("custom_recognition") in {GUARD, READY} or any(
            requires_guard(child) for child in value.values()
        )
    if isinstance(value, list):
        return any(requires_guard(child) for child in value)
    return False


def screen_size(script: dict[str, Any]) -> tuple[int, int] | None:
    target = script.get("target", {})
    if not isinstance(target, dict) or "screen_size" not in target:
        return None
    size = target["screen_size"]
    if (
        not isinstance(size, dict)
        or set(size) != {"width", "height"}
        or any(
            type(size.get(k)) is not int or not 1 <= size[k] <= 8192 for k in ("width", "height")
        )
        or size["width"] * size["height"] > 16_777_216
    ):
        raise ValueError("Invalid native screen size")
    return size["width"], size["height"]


def guard_pipeline(pipeline: dict[str, dict[str, Any]], size: tuple[int, int] | None) -> None:
    if size is None:
        return
    for name, node in list(pipeline.items()):
        if node.get("recognition") == "DirectHit" and node.get("action") == "StartApp":
            ready = name + "_OrientationReady"
            if ready not in pipeline:
                pipeline[ready] = {
                    "recognition": "Custom",
                    "custom_recognition": READY,
                    "custom_recognition_param": {"width": size[0], "height": size[1], "key": ready},
                    "action": "DoNothing",
                    "next": node.get("next", []),
                    "timeout": 11000,
                    "rate_limit": 100,
                    "attach": copy.deepcopy(node.get("attach", {})),
                }
                node["next"] = [ready]
        if name.endswith("_ScreenSource") or node.get("custom_recognition") == GUARD:
            continue
        if node.get("custom_recognition") == READY:
            continue
        if node.get("recognition") == "DirectHit" and node.get("custom_action") in {
            "MaaProjectStart",
            "MaaProjectFeedback",
            "MaaProjectScreenshot",
        }:
            continue
        if node.get("recognition") == "DirectHit" and node.get("action") in {
            "DoNothing",
            "StartApp",
            "StopApp",
            "ClickKey",
        }:
            continue
        source = name + "_ScreenSource"
        pipeline[source] = {**copy.deepcopy(node), "action": "DoNothing", "next": []}
        node.update(
            recognition="Custom",
            custom_recognition=GUARD,
            custom_recognition_param={
                "width": size[0],
                "height": size[1],
                "source": source,
                "optional_popup": bool(node.get("attach", {}).get("popup_key")),
            },
        )


def register_guard(resource: Any, recognition_type: Any, failures: list[dict[str, Any]]) -> None:
    class Guard(recognition_type):  # type: ignore[misc]  # Runtime Maa callback base.
        def analyze(self, context: Any, argv: Any) -> Any:
            try:
                return self._analyze(context, argv)
            except Exception as exc:
                # ctypes must never receive an uncaught Python exception: its
                # undefined callback return can otherwise be treated as a hit.
                if not failures:
                    failures.append({"guard_error_type": type(exc).__name__})
                return None

        def _analyze(self, context: Any, argv: Any) -> Any:
            config = json.loads(argv.custom_recognition_param)
            height, width = argv.image.shape[:2]
            if failures or (width, height) != (config["width"], config["height"]):
                if config.get("optional_popup"):
                    return None
                if not failures:
                    failures.append(
                        {
                            "expected_width": config["width"],
                            "expected_height": config["height"],
                            "actual_width": width,
                            "actual_height": height,
                        }
                    )
                return None
            result = context.run_recognition(config["source"], argv.image)
            if result is None or not result.hit:
                return None
            box = result.box
            rect = (
                (box.x, box.y, box.w, box.h)
                if hasattr(box, "x")
                else tuple(box)
                if box is not None
                else (0, 0, 0, 0)
            )
            return recognition_type.AnalyzeResult(box=rect, detail=result.raw_detail)

    if not resource.register_custom_recognition(GUARD, Guard()):
        raise RuntimeError("Native screen size guard registration failed")

    class OrientationReady(recognition_type):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self.started: dict[str, float] = {}

        def analyze(self, context: Any, argv: Any) -> Any:
            try:
                if failures:
                    return None
                config = json.loads(argv.custom_recognition_param)
                height, width = argv.image.shape[:2]
                expected = (config["width"], config["height"])
                if (width, height) == expected:
                    self.started.pop(config["key"], None)
                    return recognition_type.AnalyzeResult(box=(0, 0, 1, 1), detail={"ready": True})
                since = self.started.setdefault(config["key"], time.monotonic())
                if (width, height) != expected[::-1] or time.monotonic() - since >= 10:
                    failures.append(
                        {
                            "expected_width": expected[0],
                            "expected_height": expected[1],
                            "actual_width": width,
                            "actual_height": height,
                        }
                    )
                return None
            except Exception as exc:
                if not failures:
                    failures.append({"guard_error_type": type(exc).__name__})
                return None

    if not resource.register_custom_recognition(READY, OrientationReady()):
        raise RuntimeError("Screen orientation readiness registration failed")


def wait_guarded(
    tasker: Any, job: Any, failures: list[dict[str, Any]], watchdog: Any = None
) -> Any:
    """Stop outside native callbacks, retaining the tasker until all jobs finish."""
    while not job.done:
        if watchdog is not None:
            watchdog()
        if failures:
            tasker.post_stop().wait()
            break
        time.sleep(0.05)
    return job.wait()
