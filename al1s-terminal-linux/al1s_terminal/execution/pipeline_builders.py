"""Shared node constructors; all mutable graph state is explicit in the context."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from al1s_terminal.execution.pipeline_assets import PipelineAssetWriter
from al1s_terminal.execution.pipeline_types import CompilationContext, CompiledMaaTask, _StepPlan


class PipelineNames:
    START_ACTION = "MaaProjectStart"
    FEEDBACK_ACTION = "MaaProjectFeedback"
    SCREENSHOT_ACTION = "MaaProjectScreenshot"
    RANDOM_WAIT_ACTION = "MaaProjectRandomWait"
    CONDITIONAL_SKIP_CAPTURE_ACTION = "MaaProjectConditionalSkipCapture"
    MATCH_LOOP_LIMIT_ACTION = "MaaProjectMatchLoopLimit"
    FAILURE_RETRY_PROCESS_ACTION = "MaaProjectFailureRetryProcess"
    FAILURE_RETRY_LIMIT_ACTION = "MaaProjectFailureRetryLimit"
    MATCH_OFFSET_RECOGNITION = "MaaProjectMatchOffset"
    COLOR_MARKER_RECOGNITION = "MaaProjectColorMarker"
    COURSE_SCHEDULE_ACTION = "MaaProjectCourseSchedule"
    NUMERIC_RECOGNITION = "MaaProjectNumericCompare"
    YOLO_RECOGNITION = "MaaProjectYolo"


class PipelineBuilder(PipelineNames):
    def __init__(
        self,
        context: CompilationContext,
        compile_recovery: Callable[[dict[str, Any]], CompiledMaaTask],
    ) -> None:
        self.context = context
        self.compile_recovery = compile_recovery
        self.assets = PipelineAssetWriter(context.image_dir)

    def _materialize_image(self, value: str) -> str:
        return self.assets.materialize(value)

    def _simple_custom(self, index: int, step: dict[str, Any], action_name: str) -> _StepPlan:
        name = self._step_name(index, action_name)
        node = self._step_node(index, step, recognition="DirectHit", action="Custom")
        node.update(
            {
                "custom_action": action_name,
                "custom_action_param": {
                    key: value
                    for key, value in step.items()
                    if key
                    not in {
                        "template_base64",
                        "click_template_base64",
                        "post_assertion",
                    }
                },
            }
        )
        self.context.pipeline[name] = node
        return self._plan(name, index, step)

    def _key_step(self, index: int, step: dict[str, Any], keycode: int) -> _StepPlan:
        name = self._step_name(index, f"Key_{keycode}")
        node = self._step_node(index, step, recognition="DirectHit", action="ClickKey")
        node["key"] = keycode
        self.context.pipeline[name] = node
        return self._plan(name, index, step)

    def _swipe_node(
        self, index: int, step: dict[str, Any], values: dict[str, Any]
    ) -> dict[str, Any]:
        required = {"x1", "y1", "x2", "y2"}
        if not required.issubset(values):
            raise ValueError("swipe requires x1, y1, x2 and y2")
        node = self._step_node(index, step, recognition="DirectHit", action="Swipe")
        node.update(
            {
                "begin": [int(values["x1"]), int(values["y1"])],
                "end": [int(values["x2"]), int(values["y2"])],
                "duration": int(step.get("swipe_duration_ms", values.get("duration_ms", 300))),
            }
        )
        return node

    def _template_node(
        self,
        index: int | None,
        config: dict[str, Any],
        *,
        action: str,
        role: str,
    ) -> dict[str, Any]:
        node = self._node(
            recognition="TemplateMatch",
            action=action,
            next_nodes=[],
            role=role,
            attach=self._attach(index, config, role),
        )
        recognition = self._template_recognition(config)
        recognition.pop("recognition")
        node.update(recognition)
        node["pre_delay"] = 0
        return node

    def _template_recognition(self, config: dict[str, Any]) -> dict[str, Any]:
        template_value = str(config.get("template_base64") or "")
        if not template_value:
            raise ValueError("template recognition requires template_base64")
        recognition: dict[str, Any] = {
            "recognition": "TemplateMatch",
            "template": self._materialize_image(template_value),
            "threshold": float(config.get("threshold", 0.85)),
        }
        region = config.get("search_region")
        if isinstance(region, dict):
            recognition["roi"] = self._rect(region)
        return recognition

    def _step_node(
        self,
        index: int,
        step: dict[str, Any],
        *,
        recognition: str,
        action: str,
    ) -> dict[str, Any]:
        return self._node(
            recognition=recognition,
            action=action,
            next_nodes=[],
            role="step",
            attach=self._attach(index, step, "step"),
        )

    @staticmethod
    def _node(
        *,
        recognition: str,
        action: str,
        next_nodes: list[Any],
        role: str,
        attach: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "recognition": recognition,
            "action": action,
            "next": next_nodes,
            "pre_delay": 0,
            "post_delay": 0,
            "attach": {"maa_project_role": role, **(attach or {})},
        }

    def _plan(self, name: str, index: int, step: dict[str, Any]) -> _StepPlan:
        return _StepPlan(
            candidates=[name],
            exits=[name],
            nodes=[name],
            incoming_timeout_ms=self._timeout(step),
            incoming_rate_limit_ms=self._rate_limit(step),
        )

    @staticmethod
    def _attach(index: int | None, step: dict[str, Any], role: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "maa_project_role": role,
            "dsl_action": str(step.get("action") or ""),
        }
        if index is not None:
            result["dsl_step_index"] = index
        for source, target in (
            ("_module_index", "module_index"),
            ("_module_name", "module_name"),
            ("_module_step_index", "module_step_index"),
        ):
            if source in step:
                result[target] = step[source]
        return result

    def _name(self, suffix: str) -> str:
        return f"{self.context.prefix}_{suffix}"

    def _step_name(self, index: int, suffix: str) -> str:
        return self._name(f"Step_{index:03d}_{suffix}")

    @staticmethod
    def _rect(region: dict[str, Any]) -> list[int]:
        keys = ("x", "y", "width", "height")
        if not all(key in region for key in keys):
            raise ValueError("region requires x, y, width and height")
        result = [int(region[key]) for key in keys]
        if result[2] <= 0 or result[3] <= 0:
            raise ValueError("region width and height must be positive")
        return result

    @staticmethod
    def _milliseconds(value: Any) -> int:
        try:
            seconds = max(0.0, float(value))
        except (TypeError, ValueError) as exc:
            raise ValueError("duration must be a number") from exc
        return min(2_147_483_647, round(seconds * 1_000))

    def _timeout(self, step: dict[str, Any], default_seconds: float = 30) -> int:
        return max(100, self._milliseconds(step.get("timeout_seconds", default_seconds)))

    @staticmethod
    def _rate_limit(step: dict[str, Any]) -> int:
        try:
            seconds = float(step.get("poll_interval_seconds", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("poll interval must be a number") from exc
        return max(50, min(10_000, round(seconds * 1_000)))

    @staticmethod
    def _repeat_fields(step: dict[str, Any]) -> dict[str, int]:
        count = step.get("click_count", 1)
        interval = step.get("click_interval_ms", 120)
        if type(count) is not int or count < 1:
            raise ValueError("click_count must be a positive integer")
        if type(interval) is not int or not 0 <= interval <= 3_600_000:
            raise ValueError("click_interval_ms must be an integer between 0 and 3600000")
        return {"repeat": count, "repeat_delay": interval}
