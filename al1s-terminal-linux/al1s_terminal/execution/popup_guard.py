"""Count successful popup clicks per rule and main step, not recognition misses."""

from __future__ import annotations

import copy
import json
from typing import Any

from al1s_terminal.execution.action_dispatch import delegated_action

RECOGNITION = "Al1sPopupLimitRecognition"
ACTION = "Al1sPopupCountClick"


def has_popup_guard(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("custom_recognition") == RECOGNITION or any(
            has_popup_guard(child) for child in value.values()
        )
    return isinstance(value, list) and any(has_popup_guard(child) for child in value)


def wrap_popup_guards(pipeline: dict[str, dict[str, Any]]) -> None:
    for name, node in list(pipeline.items()):
        metadata = node.get("attach", {})
        key = metadata.get("popup_key")
        if not key:
            continue
        if metadata.get("popup_condition") and not name.endswith("_RepeatSource"):
            source = name + "_PopupRecognitionSource"
            pipeline[source] = {**copy.deepcopy(node), "action": "DoNothing", "next": []}
            node.update(
                recognition="Custom",
                custom_recognition=RECOGNITION,
                custom_recognition_param={
                    "source": source,
                    "key": key,
                    "step_index": metadata["dsl_step_index"],
                },
            )
        if node.get("action") == "Click":
            source = name + "_PopupActionSource"
            pipeline[source] = {
                **copy.deepcopy(node),
                "next": [],
                "repeat": 1,
                "pre_delay": 0,
                "post_delay": 0,
            }
            node.update(
                action="Custom",
                custom_action=ACTION,
                custom_action_param={"source": source, "key": key},
            )


def register_popup_guard(
    resource: Any, recognition_type: Any, action_type: Any, failures: list[dict[str, Any]]
) -> None:
    counts: dict[str, int] = {}

    def fail(exc: Exception) -> None:
        if not failures:
            failures.append({"error_type": "MaaIndependentRuleFailed", "cause": type(exc).__name__})

    class LimitRecognition(recognition_type):  # type: ignore[misc]  # Runtime Maa callback base.
        def analyze(self, context: Any, argv: Any) -> Any:
            try:
                if failures or context.tasker.stopping:
                    return None
                config = json.loads(argv.custom_recognition_param)
                result = context.run_recognition(config["source"], argv.image)
                if result is None or not result.hit:
                    return None
                if counts.get(config["key"], 0) >= 10:
                    failures.append(
                        {
                            "error_type": "MaaIndependentRuleLimit",
                            "step_index": config["step_index"],
                            "successful_clicks": 10,
                        }
                    )
                    return None
                box = result.box
                rect = (
                    (box.x, box.y, box.w, box.h)
                    if hasattr(box, "x")
                    else tuple(box or (0, 0, 0, 0))
                )
                return recognition_type.AnalyzeResult(box=rect, detail=result.raw_detail)
            except Exception as exc:
                fail(exc)
                return None

    class CountClick(action_type):  # type: ignore[misc]  # Runtime Maa callback base.
        def run(self, context: Any, argv: Any) -> bool:
            try:
                if failures or context.tasker.stopping:
                    return False
                config = json.loads(argv.custom_action_param)
                key = config["key"]
                # A repeat group may straddle ten. Do not perform an eleventh
                # click: let the next condition recognition decide if it cleared.
                if counts.get(key, 0) >= 10:
                    return True
                result = delegated_action(resource, context, argv, config["source"])
                if result is None or not result.success:
                    return False
                counts[key] = counts.get(key, 0) + 1
                return True
            except Exception as exc:
                fail(exc)
                return False

    if not resource.register_custom_recognition(RECOGNITION, LimitRecognition()):
        raise RuntimeError("Popup recognition guard registration failed")
    if not resource.register_custom_action(ACTION, CountClick()):
        raise RuntimeError("Popup action guard registration failed")
