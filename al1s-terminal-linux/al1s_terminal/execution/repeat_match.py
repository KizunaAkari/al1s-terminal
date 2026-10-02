"""Fresh image matching within a repeated action's existing budget."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any


def capture_repeat_frame(
    context: Any, config: dict[str, Any], failures: list[dict[str, Any]]
) -> Any:
    controller = context.tasker.controller
    capture = controller.post_screencap().wait()
    frame = controller.cached_image
    size = config["size"]
    if (
        not capture.succeeded
        or frame is None
        or (size is not None and list(frame.shape[:2][::-1]) != list(size))
    ):
        if not failures:
            failures.append(
                {"error_type": "MaaScreenSizeMismatch", "step_index": config["step_index"]}
            )
        return None
    return frame


def refresh_repeat_match(
    context: Any,
    argv: Any,
    config: dict[str, Any],
    failures: list[dict[str, Any]],
    ready: Callable[[], bool],
    wait: Callable[[float], bool],
) -> Any:
    while ready():
        frame = capture_repeat_frame(context, config, failures)
        if frame is None or not ready():
            return None
        result = context.run_recognition(config["source"], frame)
        if not ready():
            return None
        if result is not None and result.hit:
            return SimpleNamespace(
                task_detail=argv.task_detail,
                node_name=argv.node_name,
                box=result.box,
                reco_detail=result,
            )
        if not wait(config["poll_interval_ms"] / 1000):
            return None
    return None
