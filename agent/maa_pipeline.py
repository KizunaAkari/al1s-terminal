from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CompiledMaaTask:
    """A browser-authored script compiled to a MaaFramework pipeline override."""

    entry: str
    pipeline: dict[str, dict[str, Any]]
    image_dir: Path
    script_hash: str
    step_nodes: dict[int, list[str]]
    step_exits: dict[int, list[str]]
    node_steps: dict[str, int]
    active_packages: list[str]
    requires_ocr: bool = False
    requires_yolo: bool = False


@dataclass
class _StepPlan:
    candidates: list[Any]
    exits: list[str]
    nodes: list[str] = field(default_factory=list)
    incoming_timeout_ms: int = 20_000
    incoming_rate_limit_ms: int = 1_000


@dataclass
class _FailureRetryRoute:
    target_index: int
    max_retries: int
    process_script_name: str
    entry_name: str
    exhausted_name: str
    target_success_name: str
    recovery_success_name: str
    process_entry: str
    process_pipeline: dict[str, dict[str, Any]]

    @property
    def error_candidates(self) -> list[str]:
        return [self.entry_name, self.exhausted_name]


class MaaPipelineCompiler:
    """Translate the web editor DSL into MaaFramework's native pipeline model.

    MaaFramework owns recognition polling, actions, retries inside a node, and
    jump-back popup handling. This compiler only maps product-level editor
    concepts to those primitives and materializes embedded image assets.
    """

    START_ACTION = "MaaProjectStart"
    FEEDBACK_ACTION = "MaaProjectFeedback"
    SCREENSHOT_ACTION = "MaaProjectScreenshot"
    MATCH_LOOP_LIMIT_ACTION = "MaaProjectMatchLoopLimit"
    FAILURE_RETRY_PROCESS_ACTION = "MaaProjectFailureRetryProcess"
    FAILURE_RETRY_LIMIT_ACTION = "MaaProjectFailureRetryLimit"
    MATCH_OFFSET_RECOGNITION = "MaaProjectMatchOffset"
    NUMERIC_RECOGNITION = "MaaProjectNumericCompare"
    YOLO_RECOGNITION = "MaaProjectYolo"

    def __init__(self, workdir: str | Path):
        self.workdir = Path(workdir)
        self.cache_root = self.workdir / "maa" / "compiled"

    def compile(self, script: dict[str, Any]) -> CompiledMaaTask:
        steps = script.get("steps", [])
        if not isinstance(steps, list):
            raise ValueError("steps must be an array")
        self._step_count = len(steps)

        canonical = json.dumps(script, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        script_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        bundle_dir = self.cache_root / script_hash
        image_dir = bundle_dir / "image"
        pipeline_dir = bundle_dir / "pipeline"
        image_dir.mkdir(parents=True, exist_ok=True)
        pipeline_dir.mkdir(parents=True, exist_ok=True)

        self._prefix = f"Web_{script_hash}"
        self._pipeline: dict[str, dict[str, Any]] = {}
        self._image_dir = image_dir
        self._step_nodes: dict[int, list[str]] = {}
        self._step_exits: dict[int, list[str]] = {}
        self._node_steps: dict[str, int] = {}
        self._active_packages: set[str] = set()
        self._skip_guard_jumps: list[tuple[str, int]] = []
        self._requires_ocr = False
        self._requires_yolo = False

        global_candidates_by_step = self._compile_global_popups(
            script.get("global_popups", []),
            len(steps),
        )
        plans: list[_StepPlan] = []
        for index, step in enumerate(steps):
            self._global_candidates = list(global_candidates_by_step.get(index, []))
            plans.append(self._compile_step(index, step))

        failure_retry_routes = self._compile_failure_retry_routes(
            steps,
            plans,
            global_candidates_by_step,
        )

        end_name = self._name("End")
        self._pipeline[end_name] = self._node(
            recognition="DirectHit",
            action="DoNothing",
            next_nodes=[],
            role="end",
        )

        normal_indexes = list(range(len(plans)))
        for position, index in enumerate(normal_indexes):
            next_index = (
                normal_indexes[position + 1]
                if position + 1 < len(normal_indexes)
                else None
            )
            next_candidates = (
                self._entry_candidates(next_index, plans, global_candidates_by_step)
                if next_index is not None
                else [end_name]
            )
            next_timeout_ms = (
                plans[next_index].incoming_timeout_ms
                if next_index is not None
                else 1_000
            )
            next_rate_limit_ms = (
                plans[next_index].incoming_rate_limit_ms
                if next_index is not None
                else 100
            )
            for exit_name in self._step_exits[index]:
                node = self._pipeline[exit_name]
                node["next"] = list(next_candidates)
                node["timeout"] = next_timeout_ms
                node["rate_limit"] = next_rate_limit_ms
                if next_index in failure_retry_routes:
                    self._append_on_error(
                        node,
                        failure_retry_routes[next_index].error_candidates,
                    )

        # A condition can jump over a selected contiguous range of later steps.
        # The guard is the first candidate for its step, so this branch is only
        # taken when the condition recognition hits; a miss follows the normal
        # next-step link.
        for guard_name, target_index in self._skip_guard_jumps:
            guard = self._pipeline[guard_name]
            target_candidates = self._entry_candidates(
                target_index,
                plans,
                global_candidates_by_step,
            )
            guard["next"] = list(target_candidates)
            guard["timeout"] = plans[target_index].incoming_timeout_ms
            guard["rate_limit"] = plans[target_index].incoming_rate_limit_ms

        root_name = self._name("Root")
        first_index = normal_indexes[0] if normal_indexes else None
        first_candidates = (
            self._entry_candidates(first_index, plans, global_candidates_by_step)
            if first_index is not None
            else [end_name]
        )
        self._pipeline[root_name] = self._node(
            recognition="DirectHit",
            action="DoNothing",
            next_nodes=first_candidates,
            role="root",
        )
        self._pipeline[root_name]["timeout"] = (
            plans[first_index].incoming_timeout_ms
            if first_index is not None
            else 1_000
        )
        self._pipeline[root_name]["rate_limit"] = (
            plans[first_index].incoming_rate_limit_ms
            if first_index is not None
            else 100
        )
        if first_index in failure_retry_routes:
            self._append_on_error(
                self._pipeline[root_name],
                failure_retry_routes[first_index].error_candidates,
            )

        compiled_path = pipeline_dir / "compiled.json"
        compiled_path.write_text(
            json.dumps(self._pipeline, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return CompiledMaaTask(
            entry=root_name,
            pipeline=self._pipeline,
            image_dir=image_dir,
            script_hash=script_hash,
            step_nodes=self._step_nodes,
            step_exits=self._step_exits,
            node_steps=self._node_steps,
            active_packages=sorted(self._active_packages),
            requires_ocr=self._requires_ocr,
            requires_yolo=self._requires_yolo,
        )

    def _compile_failure_retry_routes(
        self,
        steps: list[Any],
        plans: list[_StepPlan],
        global_candidates_by_step: dict[int, list[Any]],
    ) -> dict[int, _FailureRetryRoute]:
        routes: dict[int, _FailureRetryRoute] = {}

        for target_index, raw_step in enumerate(steps):
            if not isinstance(raw_step, dict):
                continue
            config = raw_step.get("failure_retry")
            if not isinstance(config, dict) or config.get("enabled") is not True:
                continue
            process_script_name = str(config.get("process_script_name") or "").strip()
            if not process_script_name:
                raise ValueError("failure retry requires process_script_name")
            process_script = config.get("process_script")
            if not isinstance(process_script, dict):
                raise ValueError("failure retry process script was not resolved by the platform")
            if str(process_script.get("script_type") or "") != "module_process":
                raise ValueError("failure retry only supports module_process scripts")
            process_steps = process_script.get("steps")
            if not isinstance(process_steps, list) or not process_steps:
                raise ValueError("failure retry process script must contain at least one step")

            raw_max_retries = config.get("max_retries", 2)
            try:
                max_retries = int(raw_max_retries)
                integral = (
                    not isinstance(raw_max_retries, bool)
                    and math.isfinite(float(raw_max_retries))
                    and float(raw_max_retries) == max_retries
                )
            except (TypeError, ValueError):
                integral = False
                max_retries = 0
            if not integral or not 1 <= max_retries <= 20:
                raise ValueError(
                    "failure retry max_retries must be an integer between 1 and 20"
                )

            process_compiled = MaaPipelineCompiler(self.workdir).compile(process_script)
            for source in process_compiled.image_dir.iterdir():
                if source.is_file():
                    target = self._image_dir / source.name
                    if not target.exists():
                        shutil.copy2(source, target)
            self._requires_ocr = self._requires_ocr or process_compiled.requires_ocr
            self._requires_yolo = self._requires_yolo or process_compiled.requires_yolo
            self._active_packages.update(process_compiled.active_packages)

            route = _FailureRetryRoute(
                target_index=target_index,
                max_retries=max_retries,
                process_script_name=process_script_name,
                entry_name=self._step_name(target_index, "FailureRetryProcess"),
                exhausted_name=self._step_name(target_index, "FailureRetryLimit"),
                target_success_name=self._step_name(target_index, "FailureRetrySucceeded"),
                recovery_success_name=self._step_name(target_index, "FailureRecoverySucceeded"),
                process_entry=process_compiled.entry,
                process_pipeline=process_compiled.pipeline,
            )
            routes[target_index] = route

        for target_index, route in routes.items():
            target_plan = plans[target_index]

            entry = self._node(
                recognition="DirectHit",
                action="Custom",
                next_nodes=[route.recovery_success_name],
                role="failure-retry-process",
                attach={
                    "dsl_step_index": target_index,
                    "dsl_action": "failure_retry_process",
                    "failure_retry_process_script": route.process_script_name,
                    "failure_retry_max_retries": route.max_retries,
                },
            )
            entry.update({
                "max_hit": route.max_retries,
                "custom_action": self.FAILURE_RETRY_PROCESS_ACTION,
                "custom_action_param": {
                    "target_step_index": target_index,
                    "process_script_name": route.process_script_name,
                    "entry": route.process_entry,
                    "pipeline": route.process_pipeline,
                },
                "timeout": 1_000,
                "rate_limit": 100,
            })
            self._pipeline[route.entry_name] = entry

            exhausted = self._node(
                recognition="DirectHit",
                action="Custom",
                next_nodes=[],
                role="failure-retry-limit",
                attach={
                    "dsl_step_index": target_index,
                    "dsl_action": "failure_retry_limit",
                    "failure_retry_process_script": route.process_script_name,
                    "failure_retry_max_retries": route.max_retries,
                },
            )
            exhausted.update({
                "custom_action": self.FAILURE_RETRY_LIMIT_ACTION,
                "custom_action_param": {
                    "target_step_index": target_index,
                    "process_script_name": route.process_script_name,
                    "max_retries": route.max_retries,
                },
            })
            self._pipeline[route.exhausted_name] = exhausted

            target_success = self._node(
                recognition="DirectHit",
                action="DoNothing",
                next_nodes=[],
                role="failure-retry-target-succeeded",
                attach={
                    "dsl_step_index": target_index,
                    "dsl_action": "failure_retry_target_succeeded",
                },
            )
            self._pipeline[route.target_success_name] = target_success

            recovery_success = self._node(
                recognition="DirectHit",
                action="DoNothing",
                next_nodes=self._entry_candidates(
                    target_index,
                    plans,
                    global_candidates_by_step,
                ),
                role="failure-retry-process-succeeded",
                attach={
                    "dsl_step_index": target_index,
                    "dsl_action": "failure_retry_process_succeeded",
                    "failure_retry_process_script": route.process_script_name,
                },
            )
            recovery_success.update({
                "timeout": target_plan.incoming_timeout_ms,
                "rate_limit": target_plan.incoming_rate_limit_ms,
            })
            self._append_on_error(recovery_success, route.error_candidates)
            self._pipeline[route.recovery_success_name] = recovery_success

            for node_name in target_plan.nodes:
                self._append_on_error(
                    self._pipeline[node_name],
                    route.error_candidates,
                )
            for exit_name in target_plan.exits:
                target_exit = self._pipeline[exit_name]
                target_exit.update({
                    "next": [route.target_success_name],
                    "timeout": 1_000,
                    "rate_limit": 100,
                })

            self._step_nodes[target_index].extend([
                route.entry_name,
                route.recovery_success_name,
                route.target_success_name,
                route.exhausted_name,
            ])
            self._step_exits[target_index] = [route.target_success_name]
            self._node_steps[route.target_success_name] = target_index
            self._node_steps[route.exhausted_name] = target_index
            self._node_steps[route.entry_name] = target_index
            self._node_steps[route.recovery_success_name] = target_index

        return routes

    @staticmethod
    def _entry_candidates(
        index: int,
        plans: list[_StepPlan],
        global_candidates_by_step: dict[int, list[Any]],
    ) -> list[Any]:
        return [
            *global_candidates_by_step.get(index, []),
            *plans[index].candidates,
        ]

    @staticmethod
    def _append_on_error(node: dict[str, Any], candidates: list[Any]) -> None:
        existing = node.get("on_error", [])
        if isinstance(existing, (str, dict)):
            values = [existing]
        elif isinstance(existing, list):
            values = list(existing)
        else:
            values = []
        existing_names = {
            item.get("name") if isinstance(item, dict) else item
            for item in values
        }
        values.extend(
            candidate
            for candidate in candidates
            if (
                candidate.get("name") if isinstance(candidate, dict) else candidate
            ) not in existing_names
        )
        node["on_error"] = values

    def _compile_step(self, index: int, raw_step: Any) -> _StepPlan:
        if not isinstance(raw_step, dict):
            raise ValueError(f"step {index + 1} must be an object")
        step = raw_step
        action = str(step.get("action") or "")
        if not action:
            raise ValueError(f"step {index + 1} is missing action")

        compiler = getattr(self, f"_step_{action}", None)
        if not compiler:
            raise ValueError(f"MaaFramework backend does not support action: {action}")
        plan: _StepPlan = compiler(index, step)

        assertion = step.get("post_assertion")
        if isinstance(assertion, dict) and assertion.get("enabled") is True:
            plan = self._wrap_post_assertion(index, assertion, plan)

        condition = step.get("skip_condition")
        if isinstance(condition, dict) and condition.get("enabled") is True and action != "start":
            mode = str(condition.get("mode") or "numeric")
            if mode == "numeric":
                plan = self._wrap_numeric_skip(index, condition, plan)
            elif mode == "image":
                plan = self._wrap_image_skip(index, condition, plan)
            else:
                raise ValueError("skip condition mode must be numeric or image")

        self._step_nodes[index] = list(plan.nodes)
        self._step_exits[index] = list(plan.exits)
        for node_name in plan.nodes:
            self._node_steps[node_name] = index
        return plan

    def _step_start(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._simple_custom(index, step, self.START_ACTION)

    def _step_feedback(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._simple_custom(index, step, self.FEEDBACK_ACTION)

    def _step_wake(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._key_step(index, step, 224)

    def _step_sleep(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._key_step(index, step, 223)

    def _step_home(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._key_step(index, step, 3)

    def _step_back(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._key_step(index, step, 4)

    def _step_reboot(self, index: int, step: dict[str, Any]) -> _StepPlan:
        name = self._step_name(index, "Reboot")
        node = self._step_node(index, step, recognition="DirectHit", action="Shell")
        node.update({"cmd": "reboot", "shell_timeout": 10_000})
        self._pipeline[name] = node
        return self._plan(name, index, step)

    def _step_screenshot(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._simple_custom(index, step, self.SCREENSHOT_ACTION)

    def _step_log(self, index: int, step: dict[str, Any]) -> _StepPlan:
        name = self._step_name(index, "Log")
        self._pipeline[name] = self._step_node(
            index, step, recognition="DirectHit", action="DoNothing"
        )
        return self._plan(name, index, step)

    def _step_launch_app(self, index: int, step: dict[str, Any]) -> _StepPlan:
        package = str(step.get("package") or "").strip()
        if not package:
            raise ValueError("launch_app requires package")
        activity = str(step.get("activity") or "").strip()
        launch_entry = f"{package}/{activity}" if activity and "/" not in activity else (activity or package)
        self._active_packages.add(package)

        start_name = self._step_name(index, "StartApp")
        start_node = self._step_node(index, step, recognition="DirectHit", action="StartApp")
        start_node["package"] = launch_entry
        start_node["post_delay"] = self._milliseconds(step.get("wait_seconds", 0))
        self._pipeline[start_name] = start_node

        if step.get("force_stop_before_launch", True) is False:
            return self._plan(start_name, index, step)

        stop_name = self._step_name(index, "ColdStop")
        stop_node = self._step_node(index, step, recognition="DirectHit", action="StopApp")
        stop_node.update({"package": package, "next": [start_name], "timeout": 2_000, "rate_limit": 100})
        self._pipeline[stop_name] = stop_node
        return _StepPlan(
            candidates=[stop_name],
            exits=[start_name],
            nodes=[stop_name, start_name],
            incoming_timeout_ms=self._timeout(step),
            incoming_rate_limit_ms=self._rate_limit(step),
        )

    def _step_close_app(self, index: int, step: dict[str, Any]) -> _StepPlan:
        package = str(step.get("package") or "").strip()
        if not package:
            raise ValueError("close_app requires package")
        name = self._step_name(index, "StopApp")
        node = self._step_node(index, step, recognition="DirectHit", action="StopApp")
        node["package"] = package
        self._pipeline[name] = node
        return self._plan(name, index, step)

    def _step_tap(self, index: int, step: dict[str, Any]) -> _StepPlan:
        if "x" not in step or "y" not in step:
            raise ValueError("tap requires x and y")
        name = self._step_name(index, "Click")
        node = self._step_node(index, step, recognition="DirectHit", action="Click")
        node.update(self._repeat_fields(step))
        node["target"] = [int(step["x"]), int(step["y"])]
        self._pipeline[name] = node
        return self._plan(name, index, step)

    def _step_swipe(self, index: int, step: dict[str, Any]) -> _StepPlan:
        name = self._step_name(index, "Swipe")
        self._pipeline[name] = self._swipe_node(index, step, step)
        return self._plan(name, index, step)

    def _step_wait(self, index: int, step: dict[str, Any]) -> _StepPlan:
        name = self._step_name(index, "Wait")
        node = self._step_node(index, step, recognition="DirectHit", action="DoNothing")
        node["post_delay"] = self._milliseconds(step.get("seconds", 1))
        self._pipeline[name] = node
        return self._plan(name, index, step)

    def _step_wait_image(self, index: int, step: dict[str, Any]) -> _StepPlan:
        name = self._step_name(index, "WaitImage")
        node = self._template_node(index, step, action="DoNothing", role="step")
        self._pipeline[name] = node
        return self._plan(name, index, step)

    def _step_wait_click(self, index: int, step: dict[str, Any]) -> _StepPlan:
        condition_name = self._step_name(index, "WaitClick")
        click_mode = str(step.get("click_mode") or "fixed")

        if click_mode == "match_offset":
            return self._step_match_offset_click(index, step)

        if click_mode == "image":
            condition_node = self._template_node(index, step, action="DoNothing", role="step")
            click_template = str(step.get("click_template_base64") or "")
            if not click_template:
                raise ValueError("image click mode requires click_template_base64")
            click_name = self._step_name(index, "ClickImage")
            click_config = {
                **step,
                "template_base64": click_template,
                "threshold": step.get("click_threshold", step.get("threshold", 0.85)),
                "search_region": step.get("click_search_region"),
            }
            click_node = self._template_node(index, click_config, action="Click", role="step-click")
            click_node.update(self._repeat_fields(step))
            click_node["target"] = True
            condition_node.update({
                "next": [click_name],
                "timeout": self._milliseconds(step.get("click_timeout_seconds", step.get("timeout_seconds", 30))),
                "rate_limit": self._rate_limit(step),
            })
            self._pipeline[condition_name] = condition_node
            self._pipeline[click_name] = click_node
            return _StepPlan(
                candidates=[condition_name],
                exits=[click_name],
                nodes=[condition_name, click_name],
                incoming_timeout_ms=self._timeout(step),
                incoming_rate_limit_ms=self._rate_limit(step),
            )

        node = self._template_node(index, step, action="Click", role="step")
        node.update(self._repeat_fields(step))
        if click_mode in {"match_center", "template_center"}:
            node["target"] = True
        else:
            click = step.get("click")
            if not isinstance(click, dict) or "x" not in click or "y" not in click:
                raise ValueError("fixed click mode requires click.x and click.y")
            node["target"] = [int(click["x"]), int(click["y"])]
        self._pipeline[condition_name] = node
        return self._plan(condition_name, index, step)

    def _step_match_offset_click(self, index: int, step: dict[str, Any]) -> _StepPlan:
        template_rect = step.get("template_rect")
        if not isinstance(template_rect, dict):
            raise ValueError("match offset click requires template_rect")
        rect = self._rect(template_rect)
        width, height = rect[2], rect[3]

        order = str(step.get("match_order") or "Vertical")
        if order not in {"Horizontal", "Vertical", "Score"}:
            raise ValueError("match order must be Horizontal, Vertical or Score")
        try:
            match_index = int(step.get("match_index", 1))
            max_clicks = int(step.get("match_max_clicks", 50))
            offset_x = int(step.get("match_offset_x", 0))
            offset_y = int(step.get("match_offset_y", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("match index, offsets and max clicks must be integers") from exc
        if not 1 <= match_index <= 100:
            raise ValueError("match index must be between 1 and 100")
        if not 1 <= max_clicks <= 200:
            raise ValueError("match max clicks must be between 1 and 200")
        if abs(offset_x) > 4096 or abs(offset_y) > 4096:
            raise ValueError("match offsets must be between -4096 and 4096")

        anchor = str(step.get("match_anchor") or "center")
        anchors = {
            "top_left": (0, 0),
            "top_right": (width - 1, 0),
            "center": ((width - 1) // 2, (height - 1) // 2),
            "bottom_left": (0, height - 1),
            "bottom_right": (width - 1, height - 1),
        }
        if anchor not in anchors:
            raise ValueError("match anchor is invalid")
        anchor_x, anchor_y = anchors[anchor]
        target_offset = [
            anchor_x + offset_x,
            anchor_y + offset_y,
            1 - width,
            1 - height,
        ]

        try:
            wait_after_click = float(step.get("wait_after_click_seconds", 0.7))
        except (TypeError, ValueError) as exc:
            raise ValueError("wait after click must be a number") from exc
        if not math.isfinite(wait_after_click) or not 0.1 <= wait_after_click <= 10:
            raise ValueError("wait after click must be between 0.1 and 10 seconds")

        done_name = self._step_name(index, "MatchLoopDone")
        self._pipeline[done_name] = self._step_node(
            index,
            step,
            recognition="DirectHit",
            action="DoNothing",
        )

        overflow_name = self._step_name(index, "MatchLoopLimit")
        overflow = self._template_node(
            index,
            step,
            action="Custom",
            role="step-match-loop-limit",
        )
        overflow.update({
            "index": 0,
            "order_by": order,
            "custom_action": self.MATCH_LOOP_LIMIT_ACTION,
            "custom_action_param": {
                "max_clicks": max_clicks,
                "step_index": index,
            },
        })
        self._pipeline[overflow_name] = overflow

        click_name = self._step_name(index, "MatchLoopClick_Target")
        template_recognition = self._template_recognition(step)
        click_node = self._step_node(
            index,
            step,
            recognition="Custom",
            action="Click",
        )
        click_node.update({
            "custom_recognition": self.MATCH_OFFSET_RECOGNITION,
            "custom_recognition_param": {
                "template": template_recognition["template"],
                "threshold": template_recognition["threshold"],
                "order_by": order,
                "preferred_index": match_index - 1,
            },
        })
        if "roi" in template_recognition:
            click_node["roi"] = template_recognition["roi"]

        click_node.update({
            "target": True,
            "target_offset": target_offset,
            "post_delay": self._milliseconds(wait_after_click),
            "max_hit": max_clicks,
            "next": [*self._global_candidates, click_name, overflow_name, done_name],
            "timeout": 1_000,
            "rate_limit": self._rate_limit(step),
            **self._repeat_fields(step),
        })
        self._pipeline[click_name] = click_node

        return _StepPlan(
            candidates=[click_name, done_name],
            exits=[done_name],
            nodes=[click_name, overflow_name, done_name],
            incoming_timeout_ms=1_000,
            incoming_rate_limit_ms=self._rate_limit(step),
        )

    def _step_smart_swipe(self, index: int, step: dict[str, Any]) -> _StepPlan:
        swipe = step.get("swipe")
        if not isinstance(swipe, dict):
            raise ValueError("smart_swipe requires swipe settings")
        mode = str(step.get("mode") or "until_image")
        target_name = self._step_name(index, "SwipeTarget")
        self._pipeline[target_name] = self._template_node(index, step, action="DoNothing", role="step")

        swipe_name = self._step_name(index, "SwipeAction")
        swipe_node = self._swipe_node(index, step, swipe)
        swipe_node["post_delay"] = self._milliseconds(step.get("wait_after_swipe_seconds", 1))
        self._pipeline[swipe_name] = swipe_node

        if mode == "after_image":
            duration_ms = self._milliseconds(step.get("swipe_for_seconds", 5))
            one_cycle_ms = max(1, int(swipe_node.get("duration", 350)) + int(swipe_node["post_delay"]))
            swipe_node["repeat"] = max(1, math.ceil(duration_ms / one_cycle_ms))
            swipe_node["repeat_delay"] = int(swipe_node["post_delay"])
            swipe_node["post_delay"] = 0
            self._pipeline[target_name].update({
                "next": [swipe_name],
                "timeout": 2_000,
                "rate_limit": 100,
            })
            return _StepPlan(
                candidates=[target_name],
                exits=[swipe_name],
                nodes=[target_name, swipe_name],
                incoming_timeout_ms=self._timeout(step),
                incoming_rate_limit_ms=self._rate_limit(step),
            )

        return _StepPlan(
            candidates=[target_name, {"name": swipe_name, "jump_back": True}],
            exits=[target_name],
            nodes=[target_name, swipe_name],
            incoming_timeout_ms=self._timeout(step, default_seconds=45),
            incoming_rate_limit_ms=100,
        )

    def _step_maa(self, index: int, step: dict[str, Any]) -> _StepPlan:
        """Allow an advanced editor node to provide a native Maa pipeline node."""
        native = step.get("pipeline_node")
        if not isinstance(native, dict):
            raise ValueError("maa action requires pipeline_node")
        name = self._step_name(index, "Native")
        node = dict(native)
        node.setdefault("attach", {})
        node["attach"] = {**node["attach"], **self._attach(index, step, "step")}
        self._pipeline[name] = node
        return self._plan(name, index, step)

    def _step_yolo_detect(self, index: int, step: dict[str, Any]) -> _StepPlan:
        self._requires_yolo = True
        name = self._step_name(index, "Yolo")
        node = self._step_node(index, step, recognition="Custom", action="DoNothing")
        node.update({
            "custom_recognition": self.YOLO_RECOGNITION,
            "custom_recognition_param": {
                key: value for key, value in step.items()
                if key not in {
                    "action",
                    "skip_condition",
                    "post_assertion",
                    "template_base64",
                    "click_template_base64",
                }
            },
        })
        region = step.get("search_region")
        if isinstance(region, dict):
            node["roi"] = self._rect(region)
        self._pipeline[name] = node
        return self._plan(name, index, step)

    def _compile_global_popups(
        self,
        raw_popups: Any,
        step_count: int,
    ) -> dict[int, list[Any]]:
        if raw_popups is None:
            return {}
        if not isinstance(raw_popups, list):
            raise ValueError("global_popups must be an array")
        candidates_by_step: dict[int, list[Any]] = {}
        for index, popup in enumerate(raw_popups):
            if not isinstance(popup, dict) or popup.get("enabled", True) is False:
                continue
            raw_step_indexes = popup.get("step_indexes")
            if raw_step_indexes is None:
                step_indexes = list(range(step_count))
            else:
                if not isinstance(raw_step_indexes, list):
                    raise ValueError(f"global popup {index + 1} step_indexes must be an array")
                step_indexes = []
                for raw_step_index in raw_step_indexes:
                    if isinstance(raw_step_index, bool):
                        raise ValueError(
                            f"global popup {index + 1} step index must be an integer"
                        )
                    try:
                        one_based_index = int(raw_step_index)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            f"global popup {index + 1} step index must be an integer"
                        ) from exc
                    if one_based_index < 1 or one_based_index > step_count:
                        raise ValueError(
                            f"global popup {index + 1} step index is out of range"
                        )
                    zero_based_index = one_based_index - 1
                    if zero_based_index not in step_indexes:
                        step_indexes.append(zero_based_index)
                if not step_indexes:
                    raise ValueError(
                        f"global popup {index + 1} must select at least one step"
                    )
            name = self._name(f"Global_{index:03d}")
            click_mode = str(popup.get("click_mode") or "fixed")
            if click_mode == "image":
                condition = self._template_node(None, popup, action="DoNothing", role="global-popup")
                click_template = str(popup.get("click_template_base64") or "")
                if not click_template:
                    raise ValueError(f"global popup {index + 1} is missing click_template_base64")
                click_name = self._name(f"Global_{index:03d}_Click")
                click_config = {
                    **popup,
                    "template_base64": click_template,
                    "threshold": popup.get("click_threshold", popup.get("threshold", 0.85)),
                    "search_region": popup.get("click_search_region"),
                }
                click_node = self._template_node(None, click_config, action="Click", role="global-popup-click")
                click_node.update(self._repeat_fields(popup))
                click_node["target"] = True
                click_node["post_delay"] = self._milliseconds(popup.get("wait_after_click_seconds", 0.3))
                condition.update({"next": [click_name], "timeout": 3_000, "rate_limit": 200})
                self._pipeline[name] = condition
                self._pipeline[click_name] = click_node
            else:
                node = self._template_node(None, popup, action="Click", role="global-popup")
                node.update(self._repeat_fields(popup))
                if click_mode in {"match_center", "template_center"}:
                    node["target"] = True
                else:
                    click = popup.get("click")
                    if not isinstance(click, dict) or "x" not in click or "y" not in click:
                        raise ValueError(f"global popup {index + 1} requires click coordinates")
                    node["target"] = [int(click["x"]), int(click["y"])]
                node["post_delay"] = self._milliseconds(popup.get("wait_after_click_seconds", 0.3))
                self._pipeline[name] = node
            candidate = {"name": name, "jump_back": True}
            for step_index in step_indexes:
                candidates_by_step.setdefault(step_index, []).append(candidate)
        return candidates_by_step

    def _wrap_numeric_skip(
        self,
        index: int,
        condition: dict[str, Any],
        plan: _StepPlan,
    ) -> _StepPlan:
        operator = str(condition.get("operator") or "")
        if operator not in {"gt", "lt"}:
            raise ValueError("numeric skip operator must be gt or lt")
        try:
            value = float(condition.get("value"))
        except (TypeError, ValueError) as exc:
            raise ValueError("numeric skip value must be a number") from exc
        if not math.isfinite(value):
            raise ValueError("numeric skip value must be finite")
        region = condition.get("region")
        if not isinstance(region, dict):
            raise ValueError("numeric skip requires a region")

        self._requires_ocr = True
        guard_name = self._step_name(index, "SkipIf")
        guard = self._step_node(index, {"action": "skip_condition"}, recognition="Custom", action="DoNothing")
        guard.update({
            "custom_recognition": self.NUMERIC_RECOGNITION,
            "custom_recognition_param": {
                "operator": operator,
                "value": value,
                "region": self._rect(region),
                "step_index": index,
            },
            "roi": self._rect(region),
            "pre_delay": 0,
            "post_delay": 0,
        })
        self._pipeline[guard_name] = guard
        target_index = self._skip_target_index(index, condition)
        if target_index is not None:
            self._skip_guard_jumps.append((guard_name, target_index))
        return _StepPlan(
            candidates=[guard_name, *plan.candidates],
            exits=[guard_name, *plan.exits],
            nodes=[guard_name, *plan.nodes],
            incoming_timeout_ms=plan.incoming_timeout_ms,
            incoming_rate_limit_ms=plan.incoming_rate_limit_ms,
        )

    def _wrap_image_skip(
        self,
        index: int,
        condition: dict[str, Any],
        plan: _StepPlan,
    ) -> _StepPlan:
        template = str(condition.get("preview_base64") or "")
        if not template:
            raise ValueError("image skip requires preview_base64")
        try:
            threshold = float(condition.get("threshold", 0.85))
        except (TypeError, ValueError) as exc:
            raise ValueError("image skip threshold must be a number") from exc
        if not math.isfinite(threshold) or threshold <= 0 or threshold > 1:
            raise ValueError("image skip threshold must be between 0 and 1")

        guard_name = self._step_name(index, "SkipIfImage")
        guard = self._template_node(
            index,
            {
                "action": "skip_condition",
                "template_base64": template,
                "threshold": threshold,
            },
            action="DoNothing",
            role="skip-condition-image",
        )
        guard["post_delay"] = 0
        self._pipeline[guard_name] = guard
        target_index = self._skip_target_index(index, condition)
        if target_index is not None:
            self._skip_guard_jumps.append((guard_name, target_index))
        return _StepPlan(
            candidates=[guard_name, *plan.candidates],
            exits=[guard_name, *plan.exits],
            nodes=[guard_name, *plan.nodes],
            incoming_timeout_ms=plan.incoming_timeout_ms,
            incoming_rate_limit_ms=plan.incoming_rate_limit_ms,
        )

    def _skip_target_index(self, index: int, condition: dict[str, Any]) -> int | None:
        raw_target = condition.get("skip_to_step_index")
        if raw_target is None:
            return None
        if isinstance(raw_target, bool):
            raise ValueError("skip target step must be an integer")
        try:
            target = int(raw_target)
            numeric_target = float(raw_target)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("skip target step must be an integer") from exc
        if not math.isfinite(numeric_target) or numeric_target != target:
            raise ValueError("skip target step must be an integer")
        if target <= index + 1 or target > self._step_count:
            raise ValueError("skip target step must be a later step in the script")
        return target - 1

    def _wrap_post_assertion(
        self,
        index: int,
        assertion: dict[str, Any],
        plan: _StepPlan,
    ) -> _StepPlan:
        template = str(assertion.get("template_base64") or "")
        if not template:
            raise ValueError("post assertion requires template_base64")
        try:
            threshold = float(assertion.get("threshold", 0.85))
        except (TypeError, ValueError) as exc:
            raise ValueError("post assertion threshold must be a number") from exc
        if not 0 < threshold <= 1:
            raise ValueError("post assertion threshold must be between 0 and 1")
        raw_max_retries = assertion.get("max_retries", 2)
        try:
            max_retries = int(raw_max_retries)
        except (TypeError, ValueError) as exc:
            raise ValueError("post assertion max_retries must be an integer") from exc
        try:
            retries_are_integral = (
                not isinstance(raw_max_retries, bool)
                and math.isfinite(float(raw_max_retries))
                and float(raw_max_retries) == max_retries
            )
        except (TypeError, ValueError):
            retries_are_integral = False
        if not retries_are_integral or not 1 <= max_retries <= 20:
            raise ValueError("post assertion max_retries must be an integer between 1 and 20")

        assertion_config = {
            **assertion,
            "action": "post_assertion",
            "template_base64": template,
            "threshold": threshold,
        }
        assertion_name = self._step_name(index, "Assert")
        assertion_node = self._template_node(
            index,
            assertion_config,
            action="DoNothing",
            role="post-assertion",
        )
        assertion_node["attach"].update({
            "assertion_max_retries": max_retries,
        })
        self._pipeline[assertion_name] = assertion_node

        retry_name = self._step_name(index, "AssertRetry")
        retry_node = self._node(
            recognition="DirectHit",
            action="DoNothing",
            next_nodes=[*self._global_candidates, *plan.candidates],
            role="post-assertion-retry",
            attach={
                "dsl_step_index": index,
                "dsl_action": "post_assertion_retry",
                "assertion_max_retries": max_retries,
            },
        )
        retry_node.update({
            "max_hit": max_retries,
            "timeout": plan.incoming_timeout_ms,
            "rate_limit": plan.incoming_rate_limit_ms,
        })
        self._pipeline[retry_name] = retry_node

        assertion_timeout = self._timeout(assertion, default_seconds=3)
        assertion_rate_limit = self._rate_limit(assertion)
        for exit_name in plan.exits:
            exit_node = self._pipeline[exit_name]
            exit_node.update({
                "next": [*self._global_candidates, assertion_name],
                "on_error": [retry_name],
                "timeout": assertion_timeout,
                "rate_limit": assertion_rate_limit,
            })

        return _StepPlan(
            candidates=list(plan.candidates),
            exits=[assertion_name],
            nodes=[*plan.nodes, assertion_name, retry_name],
            incoming_timeout_ms=plan.incoming_timeout_ms,
            incoming_rate_limit_ms=plan.incoming_rate_limit_ms,
        )

    def _simple_custom(self, index: int, step: dict[str, Any], action_name: str) -> _StepPlan:
        name = self._step_name(index, action_name)
        node = self._step_node(index, step, recognition="DirectHit", action="Custom")
        node.update({
            "custom_action": action_name,
            "custom_action_param": {
                key: value for key, value in step.items()
                if key not in {
                    "template_base64",
                    "click_template_base64",
                    "post_assertion",
                }
            },
        })
        self._pipeline[name] = node
        return self._plan(name, index, step)

    def _key_step(self, index: int, step: dict[str, Any], keycode: int) -> _StepPlan:
        name = self._step_name(index, f"Key_{keycode}")
        node = self._step_node(index, step, recognition="DirectHit", action="ClickKey")
        node["key"] = keycode
        self._pipeline[name] = node
        return self._plan(name, index, step)

    def _swipe_node(self, index: int, step: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
        required = {"x1", "y1", "x2", "y2"}
        if not required.issubset(values):
            raise ValueError("swipe requires x1, y1, x2 and y2")
        node = self._step_node(index, step, recognition="DirectHit", action="Swipe")
        node.update({
            "begin": [int(values["x1"]), int(values["y1"])],
            "end": [int(values["x2"]), int(values["y2"])],
            "duration": int(step.get("swipe_duration_ms", values.get("duration_ms", 300))),
        })
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

    def _materialize_image(self, value: str) -> str:
        header = ""
        encoded = value
        if "," in value:
            header, encoded = value.split(",", 1)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("template image is not valid base64") from exc
        if not data:
            raise ValueError("template image is empty")
        extension = ".png"
        lower_header = header.lower()
        if "image/jpeg" in lower_header or data.startswith(b"\xff\xd8\xff"):
            extension = ".jpg"
        elif "image/webp" in lower_header or data.startswith(b"RIFF"):
            extension = ".webp"
        digest = hashlib.sha256(data).hexdigest()
        name = f"{digest}{extension}"
        path = self._image_dir / name
        if not path.exists():
            path.write_bytes(data)
        return name

    def _name(self, suffix: str) -> str:
        return f"{self._prefix}_{suffix}"

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
        return min(2_147_483_647, int(round(seconds * 1_000)))

    def _timeout(self, step: dict[str, Any], default_seconds: float = 30) -> int:
        return max(100, self._milliseconds(step.get("timeout_seconds", default_seconds)))

    @staticmethod
    def _rate_limit(step: dict[str, Any]) -> int:
        try:
            seconds = float(step.get("poll_interval_seconds", 1))
        except (TypeError, ValueError) as exc:
            raise ValueError("poll interval must be a number") from exc
        return max(50, min(10_000, int(round(seconds * 1_000))))

    @staticmethod
    def _repeat_fields(step: dict[str, Any]) -> dict[str, int]:
        count = max(1, min(20, int(step.get("click_count", 1))))
        interval = max(0, min(10_000, int(step.get("click_interval_ms", 120))))
        return {"repeat": count, "repeat_delay": interval}
