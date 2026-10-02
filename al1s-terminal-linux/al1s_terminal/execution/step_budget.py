"""Per-step elapsed time, excluding bounded independent-rule handling.

Recognition timeouts become failed actions so Maa's existing on_error routes
still handle recovery. No task stop is called from a native callback.
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Callable
from typing import Any

from al1s_terminal.execution.action_dispatch import delegated_action
from al1s_terminal.execution.image_match_streak import ImageMatchStreak, consecutive_match_count

RECOGNITION = "Al1sStepBudgetRecognition"
ACTION = "Al1sStepBudgetAction"


class StepClock:
    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self.now = now
        self.elapsed: dict[str, float] = {}
        self.active: str | None = None
        self.last = now()
        self.paused = False
        self.rule: str | None = None
        self.rule_started = self.last
        self.budget = 14400.0
        self.rule_budget = 30.0
        self.last_activity = self.last

    def watchdog(self, failures: list[dict[str, Any]]) -> None:
        # Normal expiry is handled by the callback's on_error. Only a stalled
        # capture/native callback needs an outer stop after a short grace.
        if (
            self.active is not None
            and self.remaining() <= 0
            and self.now() - self.last_activity > 2
            and not failures
        ):
            failures.append({"error_type": "MaaStepExecutionStalled"})

    def remaining(self) -> float:
        if self.rule is not None:
            return self.rule_budget - (self.now() - self.rule_started)
        return self.budget - self.spent(self.active) if self.active is not None else self.budget

    def enter(self, key: str, rule: str | None = None) -> None:
        current = self.now()
        self.last_activity = current
        if self.active is not None and not self.paused:
            self.elapsed[self.active] = self.elapsed.get(self.active, 0) + current - self.last
        self.active, self.paused, self.last = key, rule is not None, current
        if rule != self.rule:
            self.rule, self.rule_started = rule, current

    def spent(self, key: str) -> float:
        return self.elapsed.get(key, 0) + (
            self.now() - self.last if self.active == key and not self.paused else 0
        )

    def reset(self, key: str) -> None:
        self.elapsed[key] = 0
        self.active, self.last, self.paused, self.rule = key, self.now(), False, None

    def finish_failed_step(self, key: str) -> None:
        if self.active != key or self.rule is not None:
            return
        self.elapsed[key] = self.spent(key)
        self.active = None
        self.last = self.last_activity = self.now()
        self.paused = False


def has_step_budget(value: Any) -> bool:
    if isinstance(value, dict):
        return value.get("custom_recognition") == RECOGNITION or any(
            has_step_budget(child) for child in value.values()
        )
    return isinstance(value, list) and any(has_step_budget(child) for child in value)


def wrap_step_budgets(pipeline: dict[str, dict[str, Any]], steps: list[Any], prefix: str) -> None:
    candidates = {}
    for name, node in list(pipeline.items()):
        metadata = node.get("attach", {})
        index = metadata.get("dsl_step_index")
        if type(index) is not int or not 0 <= index < len(steps):
            continue
        if name.endswith(
            ("_ScreenSource", "_RepeatSource", "_PopupRecognitionSource", "_PopupActionSource")
        ):
            continue
        role = metadata.get("maa_project_role", "")
        if str(role).startswith("failure-retry-") and role != "failure-retry-process-succeeded":
            continue
        step = steps[index]
        default = max(
            45.0 if step.get("action") == "smart_swipe" else 30.0,
            float(step.get("seconds", 0)) + 1,
            float(step.get("wait_seconds", 0)) + 1,
            float(step.get("course_action_timeout_seconds", 0)) + 1,
            float(step.get("swipe_for_seconds", 0)) + 1,
        )
        budget = min(14400.0, float(step.get("timeout_seconds", default)))
        config = {
            "key": f"{prefix}:{index}",
            "budget": budget,
            "step_index": index,
            "rule": metadata.get("popup_key"),
            "rule_budget": metadata.get("popup_budget", 30),
            "condition": metadata.get("popup_condition", False),
            "reset": role == "failure-retry-process-succeeded",
        }
        if step.get("action") == "wait_image" and role == "step":
            config["consecutive_match_count"] = consecutive_match_count(step)
        reco_source, action_source = name + "_BudgetRecognitionSource", name + "_BudgetActionSource"
        pipeline[reco_source] = {**copy.deepcopy(node), "action": "DoNothing", "next": []}
        pipeline[action_source] = {
            **copy.deepcopy(node),
            "pre_delay": 0,
            "post_delay": 0,
            "next": [],
        }
        action_config = {
            **config,
            "source": action_source,
            "pre_delay": node.get("pre_delay", 0),
            "post_delay": node.get("post_delay", 0),
        }
        node.update(
            recognition="Custom",
            custom_recognition=RECOGNITION,
            custom_recognition_param={**config, "source": reco_source},
            action="Custom",
            custom_action=ACTION,
            custom_action_param=action_config,
            pre_delay=0,
            post_delay=0,
            repeat=1,
        )
        candidates[name] = config
    # Native timeout counts wall time and resets on jump-back. The guard owns
    # these timed recognition lists instead, retaining the original on_error.
    for node in pipeline.values():
        targets = node.get("next", [])
        if isinstance(targets, list) and any(
            (target.get("name") if isinstance(target, dict) else target) in candidates
            for target in targets
        ):
            node["timeout"] = -1


def register_step_budgets(
    resource: Any, recognition_type: Any, action_type: Any, failures: list[dict[str, Any]]
) -> StepClock:
    clock = StepClock()
    streak = ImageMatchStreak()

    def failed(exc: Exception) -> None:
        if not failures:
            failures.append({"error_type": "MaaStepBudgetFailed", "cause": type(exc).__name__})

    def ready(config: dict[str, Any], context: Any) -> bool:
        clock.last_activity = clock.now()
        if failures or context.tasker.stopping:
            return False
        if config["rule"]:
            if clock.now() - clock.rule_started >= config["rule_budget"]:
                failures.append(
                    {"error_type": "MaaIndependentRuleTimeout", "step_index": config["step_index"]}
                )
                return False
            return True
        return clock.spent(config["key"]) < float(config["budget"])

    class Recognition(recognition_type):  # type: ignore[misc]  # Runtime Maa callback base.
        def analyze(self, context: Any, argv: Any) -> Any:
            try:
                config = json.loads(argv.custom_recognition_param)
                if failures or context.tasker.stopping:
                    streak.reset()
                    return None
                if config["reset"]:
                    clock.reset(config["key"])
                    streak.reset()
                clock.enter(config["key"], config["rule"])
                clock.budget, clock.rule_budget = config["budget"], config["rule_budget"]
                if not ready(config, context):
                    streak.reset()
                    return (
                        None
                        if failures
                        else self.AnalyzeResult(box=(0, 0, 1, 1), detail={"step_timeout": True})
                    )
                result = context.run_recognition(config["source"], argv.image)
                if config["rule"] and config["condition"] and (result is None or not result.hit):
                    clock.enter(config["key"])
                if not config["rule"] and not ready(config, context):
                    streak.reset()
                    return self.AnalyzeResult(box=(0, 0, 1, 1), detail={"step_timeout": True})
                if not streak.accept(config, bool(result is not None and result.hit)):
                    return None
                box = result.box
                rect = (
                    (box.x, box.y, box.w, box.h)
                    if hasattr(box, "x")
                    else tuple(box)
                    if box is not None
                    else (0, 0, 0, 0)
                )
                return self.AnalyzeResult(box=rect, detail=result.raw_detail)
            except Exception as exc:
                streak.reset()
                failed(exc)
                return None

    class Action(action_type):  # type: ignore[misc]  # Runtime Maa callback base.
        def run(self, context: Any, argv: Any) -> bool:
            try:
                config = json.loads(argv.custom_action_param)
                clock.enter(config["key"], config["rule"])
                clock.budget, clock.rule_budget = config["budget"], config["rule_budget"]

                def delay(milliseconds: float) -> bool:
                    until = clock.now() + milliseconds / 1000
                    while clock.now() < until:
                        if not ready(config, context):
                            return False
                        time.sleep(min(0.05, max(0, until - clock.now())))
                    return ready(config, context)

                if not delay(config["pre_delay"]):
                    return False
                result = delegated_action(resource, context, argv, config["source"])
                if not ready(config, context):
                    return False
                return bool(result and result.success and delay(config["post_delay"]))
            except Exception as exc:
                failed(exc)
                return False

    if not resource.register_custom_recognition(RECOGNITION, Recognition()):
        raise RuntimeError("Step budget recognition registration failed")
    if not resource.register_custom_action(ACTION, Action()):
        raise RuntimeError("Step budget action registration failed")
    return clock
