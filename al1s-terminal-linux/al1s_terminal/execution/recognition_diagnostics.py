"""Bounded debug-only observations of real native recognition attempts."""

from __future__ import annotations

import math
import re
from collections import OrderedDict
from dataclasses import dataclass
from threading import Lock
from typing import Any


def _number(value: Any, low: float, high: float) -> float | None:
    if type(value) in {int, float} and math.isfinite(value) and low <= value <= high:
        return float(value)
    return None


@dataclass
class _Observation:
    context: dict[str, Any]
    rule: str | None
    misses: int = 0
    best_score: float | None = None
    pending: bool = False


class RecognitionDiagnostics:
    def __init__(self, pipeline: dict[str, dict[str, Any]]) -> None:
        self.pipeline = pipeline
        self.observations: OrderedDict[str, _Observation] = OrderedDict()
        self.lock = Lock()

    def observe(self, message: str, details: dict[str, Any]) -> None:
        if message not in {
            "Node.Recognition.Starting",
            "Node.Recognition.Failed",
            "Node.Recognition.Succeeded",
        }:
            return
        name = details.get("name")
        if not isinstance(name, str):
            return
        node = self.pipeline.get(name, {})
        algorithm = node.get("recognition")
        metadata = node.get("attach", {})
        index = metadata.get("dsl_step_index")
        if (
            algorithm not in {"TemplateMatch", "OCR"}
            or type(index) is not int
            or not 0 <= index < 1000
        ):
            return
        context: dict[str, Any] = {"step_index": index, "algorithm": algorithm}
        rule = metadata.get("popup_key")
        rule = rule if isinstance(rule, str) else None
        context["stage"] = (
            "post_assertion"
            if metadata.get("maa_project_role") == "post-assertion"
            else "click_target"
            if rule and not metadata.get("popup_condition")
            else "recognition"
        )
        if rule and (match := re.search(r"_Global_(\d+)_ForStep_", rule)):
            context["rule_index"] = int(match[1])
        threshold = node.get("threshold")
        if isinstance(threshold, list) and len(threshold) == 1:
            threshold = threshold[0]
        if (value := _number(threshold, 0, 1)) is not None:
            context["threshold"] = value
        with self.lock:
            item = self.observations.setdefault(name, _Observation(context, rule))
            self.observations.move_to_end(name)
            if len(self.observations) > 128:
                self.observations.popitem(last=False)
            self._update(item, message, details)

    @staticmethod
    def _update(item: _Observation, message: str, details: dict[str, Any]) -> None:
        item.pending = message.endswith(".Starting")
        if message.endswith(".Succeeded"):
            item.misses, item.best_score = 0, None
        elif message.endswith(".Failed"):
            item.misses = min(1_000_000, item.misses + 1)
            native = details.get("reco_details")
            detail = native.get("detail") if isinstance(native, dict) else None
            candidates = detail.get("all") if isinstance(detail, dict) else None
            if isinstance(candidates, list):
                for candidate in candidates:
                    score = (
                        _number(candidate.get("score"), -1, 1)
                        if isinstance(candidate, dict)
                        else None
                    )
                    if score is not None:
                        item.best_score = (
                            max(score, item.best_score) if item.best_score is not None else score
                        )

    def snapshot(self, clock: Any = None, failed_index: int | None = None) -> dict[str, Any] | None:
        # Select only the current clock scope. Popup-condition misses during
        # ordinary polling must not be mistaken for the failing main step.
        rule = getattr(clock, "rule", None)
        active = getattr(clock, "active", None)
        if clock is not None and isinstance(active, str):
            suffix = active.rsplit(":", 1)[-1]
            if suffix.isdigit():
                if failed_index is not None and failed_index != int(suffix):
                    return None
                failed_index = int(suffix)
        with self.lock:
            item = next(
                (
                    item
                    for item in reversed(self.observations.values())
                    if item.rule == rule
                    and (failed_index is None or item.context["step_index"] == failed_index)
                    and (item.misses or item.pending)
                ),
                None,
            )
            if item is None:
                return None
            result = {**item.context, "consecutive_misses": item.misses}
            if item.best_score is not None:
                result["best_score"] = item.best_score
        if clock is not None:
            budget = clock.rule_budget if rule else clock.budget
            elapsed = (
                clock.now() - clock.rule_started if rule else clock.spent(active) if active else 0
            )
            for key, value in (("timeout_seconds", budget), ("elapsed_seconds", elapsed)):
                if (valid := _number(value, 0, 86_400)) is not None:
                    result[key] = round(valid, 3)
        return result


def enrich_recognition_failure(
    diagnostic: dict[str, Any],
    tracker: RecognitionDiagnostics | None,
    clock: Any = None,
) -> None:
    if tracker is None or diagnostic.get("error_type") not in {
        "MaaStepExecutionStalled",
        "MaaIndependentRuleTimeout",
        "MaaStepTimeout",
        "MaaPipelineFailed",
        "PostAssertionFailed",
    }:
        return
    failed = diagnostic.get("failed_step", {})
    index = failed.get("index") if isinstance(failed, dict) else None
    observation = tracker.snapshot(clock, index)
    if observation is not None:
        diagnostic["recognition_failure"] = observation
        diagnostic["failed_step"] = {
            **(failed if isinstance(failed, dict) else {}),
            "index": observation["step_index"],
            "number": observation["step_index"] + 1,
        }


def install_recognition_sink(
    tasker: Any, pipeline: dict[str, dict[str, Any]]
) -> RecognitionDiagnostics:
    from maa.context import ContextEventSink

    tracker = RecognitionDiagnostics(pipeline)

    class RecognitionSink(ContextEventSink):  # type: ignore[misc]  # SDK has no typed stubs.
        def on_raw_notification(self, _context: Any, message: str, details: dict[str, Any]) -> None:
            tracker.observe(message, details)

    if tasker.add_context_sink(RecognitionSink()) is None:
        raise RuntimeError("Maa recognition diagnostic sink could not be installed")
    return tracker
