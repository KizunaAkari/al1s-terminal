"""Bounded repeated clicks; never silently truncate the requested count."""

from __future__ import annotations

import copy
import json
import time
from typing import Any

from al1s_terminal.execution.action_dispatch import delegated_action
from al1s_terminal.execution.repeat_match import capture_repeat_frame, refresh_repeat_match

ACTION = "Al1sRepeatedClick"


def has_repeated_click(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("custom_action") == ACTION or any(
            has_repeated_click(v) for v in value.values()
        )
    return isinstance(value, list) and any(has_repeated_click(v) for v in value)


def wrap_repeated_clicks(
    pipeline: dict[str, dict[str, Any]], steps: list[Any], size: tuple[int, int] | None
) -> None:
    for name, node in list(pipeline.items()):
        if node.get("action") not in {"Click", "Swipe"} or node.get("repeat", 1) <= 1:
            continue
        source = name + "_RepeatSource"
        pipeline[source] = {
            **copy.deepcopy(node),
            "repeat": 1,
            "repeat_delay": 0,
            "pre_delay": 0,
            "post_delay": 0,
            "next": [],
        }
        index = node.get("attach", {}).get("dsl_step_index")
        step = steps[index] if type(index) is int and 0 <= index < len(steps) else {}
        budget = min(14400.0, max(0.1, float(step.get("timeout_seconds", 30))))
        node.update(
            action="Custom",
            custom_action=ACTION,
            custom_action_param={
                "source": source,
                "count": str(node["repeat"]),
                "interval_ms": node.get("repeat_delay", 0),
                "budget_seconds": budget,
                "size": size,
                "step_index": index,
            },
            repeat=1,
            repeat_delay=0,
        )
        if node.get("attach", {}).get("click_match_center") is True:
            node["custom_action_param"].update(
                recheck_match=True,
                poll_interval_ms=max(
                    50, min(10_000, round(float(step.get("poll_interval_seconds", 1)) * 1000))
                ),
            )


def register_repeated_click(
    resource: Any, action_type: Any, failures: list[dict[str, Any]], budget_clock: Any = None
) -> None:
    class RepeatedClick(action_type):  # type: ignore[misc]  # Runtime Maa callback base.
        def run(self, context: Any, argv: Any) -> bool:
            try:
                return self._run(context, argv)
            except Exception as exc:
                if not failures:
                    failures.append(
                        {"error_type": "MaaRepeatedClickFailed", "cause": type(exc).__name__}
                    )
                return False

        def _run(self, context: Any, argv: Any) -> bool:
            config = json.loads(argv.custom_action_param)
            deadline = time.monotonic() + config["budget_seconds"]

            def ready() -> bool:
                if failures or context.tasker.stopping:
                    return False
                if budget_clock is not None and budget_clock.remaining() <= 0:
                    return False  # Outer step action retains its native on_error path.
                if budget_clock is None and time.monotonic() >= deadline:
                    failures.append(
                        {"error_type": "MaaClickRepeatTimeout", "step_index": config["step_index"]}
                    )
                    return False
                return True

            count = int(config["count"])

            def wait(seconds: float) -> bool:
                wake = time.monotonic() + seconds
                while time.monotonic() < wake:
                    if not ready():
                        return False
                    remaining = (
                        budget_clock.remaining()
                        if budget_clock is not None
                        else deadline - time.monotonic()
                    )
                    time.sleep(min(0.05, max(0, wake - time.monotonic()), max(0, remaining)))
                return ready()

            for index in range(count):
                if not ready():
                    return False
                current = argv
                if index and config.get("recheck_match"):
                    current = refresh_repeat_match(context, argv, config, failures, ready, wait)
                    if current is None:
                        return False
                elif config["size"] is not None:
                    if capture_repeat_frame(context, config, failures) is None:
                        return False
                if not ready():
                    return False
                result = delegated_action(resource, context, current, config["source"])
                if result is None or not result.success:
                    return False
                if index + 1 < count and not wait(config["interval_ms"] / 1000):
                    return False
            return ready()

    if not resource.register_custom_action(ACTION, RepeatedClick()):
        raise RuntimeError("Repeated click custom extension registration failed")
