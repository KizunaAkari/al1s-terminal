"""Wire cross-step retries, skips, assertions and global popup routes."""

from __future__ import annotations

import copy
import math
import re
import shutil
from typing import Any

from al1s_terminal.execution.pipeline_builders import PipelineBuilder
from al1s_terminal.execution.pipeline_types import CompiledMaaTask, _FailureRetryRoute, _StepPlan


class PipelineControlFlowCompiler(PipelineBuilder):
    def _compile_failure_retry_routes(
        self,
        steps: list[Any],
        plans: list[_StepPlan],
        global_candidates_by_step: dict[int, list[Any]],
    ) -> dict[int, _FailureRetryRoute]:
        routes: dict[int, _FailureRetryRoute] = {}
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            config = step.get("failure_retry")
            if isinstance(config, dict) and config.get("enabled") is True:
                routes[index] = self._prepare_retry_route(index, config)
        for index, route in routes.items():
            self._write_retry_entry(route)
            self._write_retry_exhausted(route)
            self._write_retry_success(route, plans[index], plans, global_candidates_by_step)
            self._wire_retry_target(route, plans[index])
        return routes

    def _prepare_retry_route(
        self,
        target_index: int,
        config: dict[str, Any],
    ) -> _FailureRetryRoute:
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

        max_retries = self._retry_count(config)

        process_compiled = self.compile_recovery(process_script)
        self._merge_recovery(process_compiled)

        return _FailureRetryRoute(
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

    @staticmethod
    def _retry_count(config: dict[str, Any]) -> int:
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
            raise ValueError("failure retry max_retries must be an integer between 1 and 20")

        return max_retries

    def _merge_recovery(self, process_compiled: CompiledMaaTask) -> None:
        for source in process_compiled.image_dir.iterdir():
            if source.is_file():
                target = self.context.image_dir / source.name
                if not target.exists():
                    shutil.copy2(source, target)
        self.context.requires_ocr = self.context.requires_ocr or process_compiled.requires_ocr
        self.context.requires_yolo = self.context.requires_yolo or process_compiled.requires_yolo
        self.context.active_packages.update(process_compiled.active_packages)

    def _write_retry_entry(self, route: _FailureRetryRoute) -> None:
        target_index = route.target_index
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
        entry.update(
            {
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
            }
        )
        self.context.pipeline[route.entry_name] = entry

    def _write_retry_exhausted(self, route: _FailureRetryRoute) -> None:
        target_index = route.target_index
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
        exhausted.update(
            {
                "custom_action": self.FAILURE_RETRY_LIMIT_ACTION,
                "custom_action_param": {
                    "target_step_index": target_index,
                    "process_script_name": route.process_script_name,
                    "max_retries": route.max_retries,
                },
            }
        )
        self.context.pipeline[route.exhausted_name] = exhausted

    def _write_retry_success(
        self,
        route: _FailureRetryRoute,
        target_plan: _StepPlan,
        plans: list[_StepPlan],
        global_candidates_by_step: dict[int, list[Any]],
    ) -> None:
        target_index = route.target_index
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
        self.context.pipeline[route.target_success_name] = target_success

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
        recovery_success.update(
            {
                "timeout": target_plan.incoming_timeout_ms,
                "rate_limit": target_plan.incoming_rate_limit_ms,
            }
        )
        self._append_on_error(recovery_success, route.error_candidates)
        self.context.pipeline[route.recovery_success_name] = recovery_success

    def _wire_retry_target(self, route: _FailureRetryRoute, target_plan: _StepPlan) -> None:
        target_index = route.target_index
        for node_name in target_plan.nodes:
            self._append_on_error(
                self.context.pipeline[node_name],
                route.error_candidates,
            )
        for exit_name in target_plan.exits:
            target_exit = self.context.pipeline[exit_name]
            target_exit.update(
                {
                    "next": [route.target_success_name],
                    "timeout": 1_000,
                    "rate_limit": 100,
                }
            )

        self.context.step_nodes[target_index].extend(
            [
                route.entry_name,
                route.recovery_success_name,
                route.target_success_name,
                route.exhausted_name,
            ]
        )
        self.context.step_exits[target_index] = [route.target_success_name]
        self.context.node_steps[route.target_success_name] = target_index
        self.context.node_steps[route.exhausted_name] = target_index
        self.context.node_steps[route.entry_name] = target_index
        self.context.node_steps[route.recovery_success_name] = target_index

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
        existing_names = {item.get("name") if isinstance(item, dict) else item for item in values}
        values.extend(
            candidate
            for candidate in candidates
            if (candidate.get("name") if isinstance(candidate, dict) else candidate)
            not in existing_names
        )
        node["on_error"] = values

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
                start = popup.get("from_step_index", 1)
                end = popup.get("through_step_index", step_count)
                if (
                    type(start) is not int
                    or type(end) is not int
                    or not 1 <= start <= end <= step_count
                ):
                    raise ValueError("Independent rule range is invalid")
                step_indexes = list(range(start - 1, end))
            else:
                if "from_step_index" in popup or "through_step_index" in popup:
                    raise ValueError("Independent rule cannot mix range and explicit steps")
                if not isinstance(raw_step_indexes, list):
                    raise ValueError(f"global popup {index + 1} step_indexes must be an array")
                step_indexes = []
                for raw_step_index in raw_step_indexes:
                    if isinstance(raw_step_index, bool):
                        raise ValueError(f"global popup {index + 1} step index must be an integer")
                    try:
                        one_based_index = int(raw_step_index)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            f"global popup {index + 1} step index must be an integer"
                        ) from exc
                    if one_based_index < 1 or one_based_index > step_count:
                        raise ValueError(f"global popup {index + 1} step index is out of range")
                    zero_based_index = one_based_index - 1
                    if zero_based_index not in step_indexes:
                        step_indexes.append(zero_based_index)
                if not step_indexes:
                    raise ValueError(f"global popup {index + 1} must select at least one step")
            name = self._name(f"Global_{index:03d}")
            click_mode = str(popup.get("click_mode") or "fixed")
            if click_mode == "image":
                condition = self._template_node(
                    None, popup, action="DoNothing", role="global-popup"
                )
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
                click_node = self._template_node(
                    None, click_config, action="Click", role="global-popup-click"
                )
                click_node.update(self._repeat_fields(popup))
                click_node["target"] = True
                click_node["post_delay"] = self._milliseconds(
                    popup.get("wait_after_click_seconds", 0.3)
                )
                condition.update({"next": [click_name], "timeout": 3_000, "rate_limit": 200})
                self.context.pipeline[name] = condition
                self.context.pipeline[click_name] = click_node
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
                self.context.pipeline[name] = node
            for step_index in step_indexes:
                # Each main step owns independent rule counters. Shared native
                # nodes cannot identify which jump-back caller is being handled.
                scoped = f"{name}_ForStep_{step_index:03d}"
                source_names = [name] + ([click_name] if click_mode == "image" else [])
                mapping = {source: source.replace(name, scoped, 1) for source in source_names}
                for source in source_names:
                    node = copy.deepcopy(self.context.pipeline[source])
                    node["next"] = [mapping.get(item, item) for item in node.get("next", [])]
                    node.setdefault("attach", {}).update(
                        popup_key=scoped,
                        popup_condition=source == name,
                        popup_budget=float(popup.get("timeout_seconds", 30)),
                        definition_key=popup.get("_definition_key"),
                        dsl_step_index=step_index,
                    )
                    self.context.pipeline[mapping[source]] = node
                candidate = {"name": scoped, "jump_back": True}
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
        raw_value = condition.get("value")
        if raw_value is None:
            raise ValueError("numeric skip value must be a number")
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("numeric skip value must be a number") from exc
        if not math.isfinite(value):
            raise ValueError("numeric skip value must be finite")
        region = condition.get("region")
        if not isinstance(region, dict):
            raise ValueError("numeric skip requires a region")

        self.context.requires_ocr = True
        guard_name = self._step_name(index, "SkipIf")
        guard = self._step_node(
            index, {"action": "skip_condition"}, recognition="Custom", action="Custom"
        )
        guard.update(
            {
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
                "custom_action": self.CONDITIONAL_SKIP_CAPTURE_ACTION,
                "custom_action_param": {"step_index": index, "mode": "numeric"},
            }
        )
        self.context.pipeline[guard_name] = guard
        target_index = self._skip_target_index(index, condition)
        if target_index is not None:
            self.context.skip_guard_jumps.append((guard_name, target_index))
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
            action="Custom",
            role="skip-condition-image",
        )
        guard.update(
            {
                "custom_action": self.CONDITIONAL_SKIP_CAPTURE_ACTION,
                "custom_action_param": {"step_index": index, "mode": "image"},
            }
        )
        guard["post_delay"] = 0
        self.context.pipeline[guard_name] = guard
        target_index = self._skip_target_index(index, condition)
        if target_index is not None:
            self.context.skip_guard_jumps.append((guard_name, target_index))
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
        if target <= index + 1 or target > self.context.step_count:
            raise ValueError("skip target step must be a later step in the script")
        return target - 1

    def _assertion_node(self, index: int, assertion: dict[str, Any]) -> dict[str, Any]:
        mode = assertion.get("recognition_mode", "image")
        config = {**assertion, "action": "post_assertion"}
        if mode == "text":
            text = assertion.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 200:
                raise ValueError("OCR assertion text must contain 1 to 200 characters")
            self.context.requires_ocr = True
            node = self._step_node(index, config, recognition="OCR", action="DoNothing")
            node["attach"]["maa_project_role"] = "post-assertion"
            node.update(expected=[re.escape(text)], order_by="Horizontal", index=0)
            if assertion.get("search_region") is not None:
                node["roi"] = self._rect(assertion["search_region"])
            return node
        if mode != "image":
            raise ValueError("post assertion recognition_mode must be image or text")
        template = str(assertion.get("template_base64") or "")
        if not template:
            raise ValueError("post assertion requires template_base64")
        try:
            threshold = float(assertion.get("threshold", 0.85))
        except (TypeError, ValueError) as exc:
            raise ValueError("post assertion threshold must be a number") from exc
        if not 0 < threshold <= 1:
            raise ValueError("post assertion threshold must be between 0 and 1")
        return self._template_node(
            index,
            {**config, "template_base64": template, "threshold": threshold},
            action="DoNothing",
            role="post-assertion",
        )

    def _wrap_post_assertion(
        self,
        index: int,
        assertion: dict[str, Any],
        plan: _StepPlan,
    ) -> _StepPlan:
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

        assertion_name = self._step_name(index, "Assert")
        assertion_node = self._assertion_node(index, assertion)
        assertion_node["attach"].update(
            {
                "assertion_max_retries": max_retries,
            }
        )
        self.context.pipeline[assertion_name] = assertion_node

        retry_name = self._step_name(index, "AssertRetry")
        retry_node = self._node(
            recognition="DirectHit",
            action="DoNothing",
            next_nodes=[*self.context.global_candidates, *plan.candidates],
            role="post-assertion-retry",
            attach={
                "dsl_step_index": index,
                "dsl_action": "post_assertion_retry",
                "assertion_max_retries": max_retries,
            },
        )
        retry_node.update(
            {
                "max_hit": max_retries,
                "timeout": plan.incoming_timeout_ms,
                "rate_limit": plan.incoming_rate_limit_ms,
            }
        )
        self.context.pipeline[retry_name] = retry_node

        assertion_timeout = self._timeout(assertion, default_seconds=3)
        assertion_rate_limit = self._rate_limit(assertion)
        for exit_name in plan.exits:
            exit_node = self.context.pipeline[exit_name]
            exit_node.update(
                {
                    "next": [*self.context.global_candidates, assertion_name],
                    "on_error": [retry_name],
                    "timeout": assertion_timeout,
                    "rate_limit": assertion_rate_limit,
                }
            )

        return _StepPlan(
            candidates=list(plan.candidates),
            exits=[assertion_name],
            nodes=[*plan.nodes, assertion_name, retry_name],
            incoming_timeout_ms=plan.incoming_timeout_ms,
            incoming_rate_limit_ms=plan.incoming_rate_limit_ms,
        )
