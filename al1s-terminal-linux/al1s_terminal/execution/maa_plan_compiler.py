from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from al1s_terminal.execution.maa_definition import (
    MaaDefinitionError,
    MaaExecutableModule,
    MaaExecutionPlan,
)
from al1s_terminal.execution.maa_pipeline import CompiledMaaTask, MaaPipelineCompiler


class MaaPlanCompiler:
    """Adapt the immutable registered-handler plan to the verified Maa pipeline compiler."""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir

    def compile(self, plan: MaaExecutionPlan) -> tuple[CompiledMaaTask, ...]:
        return tuple(
            MaaPipelineCompiler(self._data_dir).compile(
                self._script(module, plan, stack=()),
            )
            for module in plan.modules
        )

    def _script(
        self,
        module: MaaExecutableModule,
        plan: MaaExecutionPlan,
        *,
        stack: tuple[str, ...],
    ) -> dict[str, Any]:
        if module.definition_key in stack or len(stack) >= 20:
            raise MaaDefinitionError(
                "maa_recovery_cycle",
                "Maa recovery definition contains a cycle or exceeds the depth limit",
            )
        next_stack = (*stack, module.definition_key)
        return {
            "version": 2,
            "script_type": module.script_type,
            "target": dict(module.target),
            "steps": [self._step(item, plan, stack=next_stack) for item in module.steps],
            "global_popups": [
                {
                    **self._resolve_resources(rule, plan.resources),
                    "_definition_key": module.definition_key,
                }
                for rule in module.independent_rules
            ],
            "cleanup_on_finish": module.cleanup_on_finish,
        }

    def _step(
        self,
        value: dict[str, Any],
        plan: MaaExecutionPlan,
        *,
        stack: tuple[str, ...],
    ) -> dict[str, Any]:
        action_id = value.get("action_id")
        parameters = value.get("parameters")
        wrappers = value.get("wrappers", [])
        if not isinstance(action_id, str) or not isinstance(parameters, dict):
            raise MaaDefinitionError("maa_step_invalid", "Maa step payload is invalid")
        step = {"action": action_id, **self._resolve_resources(parameters, plan.resources)}
        for wrapper in wrappers:
            if not isinstance(wrapper, dict):
                raise MaaDefinitionError("maa_wrapper_invalid", "Maa wrapper is invalid")
            kind = wrapper.get("kind")
            raw_parameters = wrapper.get("parameters", {})
            if not isinstance(raw_parameters, dict):
                raise MaaDefinitionError("maa_wrapper_invalid", "Maa wrapper is invalid")
            resolved = self._resolve_resources(raw_parameters, plan.resources)
            if kind == "conditional_skip":
                step["skip_condition"] = resolved
            elif kind == "post_assertion":
                step["post_assertion"] = resolved
            elif kind == "wait_after_execution":
                step["wait_after_execution_seconds"] = resolved.get("seconds")
            elif kind == "system_key_wait":
                if action_id not in {"back", "home", "task_view"}:
                    raise MaaDefinitionError("maa_wrapper_invalid", "System-key wait is invalid")
                step["wait_before_execution_seconds"] = resolved.get("before_seconds")
                step["wait_after_execution_seconds"] = resolved.get("after_seconds")
            elif kind == "recognize_match_center":
                if action_id != "recognize_execute" or step.get("execution_mode") != "match_center":
                    raise MaaDefinitionError(
                        "maa_wrapper_invalid", "Image-center marker is invalid"
                    )
            elif kind == "wait_image_stability":
                if action_id != "wait_image":
                    raise MaaDefinitionError(
                        "maa_wrapper_invalid", "Image stability requires a wait_image step"
                    )
                step["consecutive_match_count"] = resolved.get("consecutive_match_count")
            elif kind == "failure_retry":
                recovery_key = resolved.get("recovery_definition_key")
                recovery = plan.definitions.get(str(recovery_key))
                if recovery is None:
                    raise MaaDefinitionError(
                        "maa_recovery_definition_missing",
                        "Maa failure recovery definition is missing",
                    )
                step["failure_retry"] = {
                    "enabled": True,
                    "max_retries": resolved.get("max_retries"),
                    "process_script_name": recovery.definition_key,
                    "process_script": self._script(recovery, plan, stack=stack),
                }
            else:
                raise MaaDefinitionError(
                    "maa_wrapper_unregistered",
                    "Maa definition requested an unregistered wrapper",
                )
        return step

    def _resolve_resources(self, value: Any, resources: dict[str, Path]) -> Any:
        if isinstance(value, dict):
            resource_key = value.get("$resource")
            if resource_key is not None:
                path = resources.get(str(resource_key))
                if path is None:
                    raise MaaDefinitionError(
                        "maa_resource_missing",
                        "Maa definition references an unavailable resource",
                    )
                media_type = str(value.get("media_type") or "image/png")
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                return f"data:{media_type};base64,{encoded}"
            return {key: self._resolve_resources(child, resources) for key, child in value.items()}
        if isinstance(value, list):
            return [self._resolve_resources(child, resources) for child in value]
        return value
