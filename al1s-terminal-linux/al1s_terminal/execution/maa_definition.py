from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


class MaaDefinitionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


REGISTERED_HANDLERS = frozenset(
    {
        "maa.pipeline.back",
        "maa.pipeline.tap",
        "maa.pipeline.swipe",
        "maa.pipeline.home",
        "maa.pipeline.task_view",
        "maa.pipeline.launch_app",
        "maa.pipeline.recognize_execute",
        "maa.pipeline.smart_swipe",
        "maa.pipeline.wait",
        "maa.pipeline.wait_click",
        "maa.pipeline.wait_image",
        "maa.pipeline.wait_text",
        "maa.pipeline.click_text",
        "maa.registered.cleanup",
        "maa.registered.course_schedule",
        "maa.registered.feedback",
        "maa.registered.screenshot",
        "maa.registered.start",
        "maa.registered.wait_random",
        "maa.wrapper.conditional_skip",
        "maa.wrapper.failure_skip",
        "maa.wrapper.failure_retry",
        "maa.wrapper.post_assertion",
        "maa.wrapper.post_assertion_text",
        "maa.wrapper.wait_after_execution",
        "maa.wrapper.system_key_wait",
        "maa.wrapper.wait_image_stability",
        "maa.wrapper.recognize_match_center",
    }
)


@dataclass(frozen=True, slots=True)
class MaaExecutableModule:
    definition_key: str
    script_version_id: str
    script_name: str
    script_type: str
    target: dict[str, Any]
    steps: tuple[dict[str, Any], ...]
    independent_rules: tuple[dict[str, Any], ...]
    cleanup_on_finish: bool
    wait_after_ms: int


@dataclass(frozen=True, slots=True)
class MaaExecutionPlan:
    definition_type: str
    modules: tuple[MaaExecutableModule, ...]
    definitions: dict[str, MaaExecutableModule]
    resources: dict[str, Path]


class MaaDefinitionLoader:
    def load(
        self,
        manifest: dict[str, Any],
        *,
        resources: dict[str, Path],
    ) -> MaaExecutionPlan:
        if manifest.get("schema_version") != 1:
            raise MaaDefinitionError(
                "maa_definition_schema_unsupported",
                "Maa execution definition schema is not supported",
            )
        definition_type = manifest.get("definition_type")
        if definition_type not in {"quick_test", "script", "strategy"}:
            raise MaaDefinitionError(
                "maa_definition_type_unsupported",
                "Maa execution definition type is not supported",
            )
        raw_definitions = manifest.get("definitions")
        if not isinstance(raw_definitions, dict) or not raw_definitions:
            raise MaaDefinitionError(
                "maa_definition_closure_missing",
                "Maa execution definition closure is missing",
            )
        definitions = {
            str(key): value
            for key, value in raw_definitions.items()
            if isinstance(key, str) and isinstance(value, dict)
        }
        if len(definitions) != len(raw_definitions):
            raise MaaDefinitionError(
                "maa_definition_closure_invalid",
                "Maa execution definition closure is invalid",
            )

        entries = self._entries(manifest, definition_type)
        compiled_definitions = {
            key: self._module(
                key,
                definitions,
                resources,
                wait_after_ms=0,
            )
            for key in definitions
        }
        modules = tuple(
            replace(compiled_definitions[key], wait_after_ms=wait_after_ms)
            for key, wait_after_ms in entries
            if key in compiled_definitions
        )
        if len(modules) != len(entries):
            raise MaaDefinitionError(
                "maa_definition_closure_missing",
                "Maa module references an absent definition",
            )
        return MaaExecutionPlan(
            definition_type,
            modules,
            compiled_definitions,
            dict(resources),
        )

    @staticmethod
    def _entries(manifest: dict[str, Any], definition_type: str) -> tuple[tuple[str, int], ...]:
        if definition_type != "strategy":
            key = manifest.get("entry_definition_key")
            if not isinstance(key, str) or not key:
                raise MaaDefinitionError(
                    "maa_entry_definition_missing",
                    "Maa entry definition is missing",
                )
            return ((key, 0),)
        raw_modules = manifest.get("modules")
        if not isinstance(raw_modules, list) or not raw_modules:
            raise MaaDefinitionError("maa_strategy_empty", "Maa strategy contains no modules")
        entries: list[tuple[int, str, int]] = []
        positions: set[int] = set()
        for raw in raw_modules:
            if not isinstance(raw, dict):
                raise MaaDefinitionError("maa_strategy_module_invalid", "Maa module is invalid")
            key = raw.get("definition_key")
            position = raw.get("position")
            wait_after_ms = raw.get("wait_after_ms", 0)
            if (
                not isinstance(key, str)
                or not key
                or not isinstance(position, int)
                or isinstance(position, bool)
                or position < 0
                or position in positions
                or not isinstance(wait_after_ms, int)
                or isinstance(wait_after_ms, bool)
                or not 0 <= wait_after_ms <= 3_600_000
            ):
                raise MaaDefinitionError(
                    "maa_strategy_module_invalid",
                    "Maa strategy module metadata is invalid",
                )
            positions.add(position)
            entries.append((position, key, wait_after_ms))
        return tuple((key, wait_after_ms) for _, key, wait_after_ms in sorted(entries))

    def _module(
        self,
        key: str,
        definitions: dict[str, dict[str, Any]],
        resources: dict[str, Path],
        *,
        wait_after_ms: int,
    ) -> MaaExecutableModule:
        raw = definitions.get(key)
        if raw is None:
            raise MaaDefinitionError(
                "maa_definition_closure_missing",
                "Maa module references an absent definition",
            )
        if raw.get("schema_version") != 1 or raw.get("compiler_version") not in {
            "maa-registered-actions-v1",
            "maa-registered-actions-v2",
        }:
            raise MaaDefinitionError(
                "maa_compiler_version_unsupported",
                "Maa compiled module version is not supported",
            )
        raw_steps = raw.get("steps")
        if not isinstance(raw_steps, list) or len(raw_steps) > 1_000:
            raise MaaDefinitionError("maa_steps_invalid", "Maa module steps are invalid")
        steps = tuple(
            self._step(item, expected_index=index, definitions=definitions, resources=resources)
            for index, item in enumerate(raw_steps, start=1)
        )
        rules = raw.get("independent_rules", [])
        if not isinstance(rules, list) or len(rules) > 50:
            raise MaaDefinitionError(
                "maa_independent_rules_invalid",
                "Maa independent rules are invalid",
            )
        if any(not isinstance(item, dict) for item in rules):
            raise MaaDefinitionError(
                "maa_independent_rules_invalid",
                "Maa independent rules are invalid",
            )
        self._require_known_resources(rules, resources)
        target = raw.get("target", {})
        if not isinstance(target, dict):
            raise MaaDefinitionError("maa_target_invalid", "Maa target is invalid")
        return MaaExecutableModule(
            definition_key=key,
            script_version_id=_required_text(raw, "script_version_id"),
            script_name=_required_text(raw, "script_name"),
            script_type=str(raw.get("script_type") or "standard"),
            target=dict(target),
            steps=steps,
            independent_rules=tuple(dict(item) for item in rules),
            cleanup_on_finish=raw.get("cleanup_on_finish") is True,
            wait_after_ms=wait_after_ms,
        )

    def _step(
        self,
        raw: object,
        *,
        expected_index: int,
        definitions: dict[str, dict[str, Any]],
        resources: dict[str, Path],
    ) -> dict[str, Any]:
        if not isinstance(raw, dict) or raw.get("step_index") != expected_index:
            raise MaaDefinitionError("maa_step_invalid", "Maa step order is invalid")
        handler_id = raw.get("handler_id")
        if handler_id not in REGISTERED_HANDLERS:
            raise MaaDefinitionError(
                "maa_handler_unregistered",
                "Maa definition requested an unregistered runtime handler",
            )
        parameters = raw.get("parameters")
        wrappers = raw.get("wrappers", [])
        if not isinstance(parameters, dict) or not isinstance(wrappers, list):
            raise MaaDefinitionError("maa_step_invalid", "Maa step payload is invalid")
        self._require_known_resources(parameters, resources)
        for wrapper in wrappers:
            if (
                not isinstance(wrapper, dict)
                or wrapper.get("handler_id") not in REGISTERED_HANDLERS
            ):
                raise MaaDefinitionError(
                    "maa_wrapper_unregistered",
                    "Maa definition requested an unregistered wrapper",
                )
            wrapper_parameters = wrapper.get("parameters", {})
            if not isinstance(wrapper_parameters, dict):
                raise MaaDefinitionError("maa_wrapper_invalid", "Maa wrapper is invalid")
            self._require_known_resources(wrapper_parameters, resources)
            recovery_key = wrapper_parameters.get("recovery_definition_key")
            if recovery_key is not None and recovery_key not in definitions:
                raise MaaDefinitionError(
                    "maa_recovery_definition_missing",
                    "Maa failure recovery definition is missing",
                )
        return dict(raw)

    def _require_known_resources(self, value: object, resources: dict[str, Path]) -> None:
        if isinstance(value, dict):
            resource_key = value.get("$resource")
            if resource_key is not None:
                if not isinstance(resource_key, str) or resource_key not in resources:
                    raise MaaDefinitionError(
                        "maa_resource_missing",
                        "Maa definition references an unavailable resource",
                    )
                path = resources[resource_key]
                if path.is_symlink() or not path.is_file():
                    raise MaaDefinitionError(
                        "maa_resource_invalid",
                        "Maa resource path is not a regular file",
                    )
            for child in value.values():
                self._require_known_resources(child, resources)
        elif isinstance(value, list):
            for child in value:
                self._require_known_resources(child, resources)


def _required_text(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise MaaDefinitionError("maa_definition_invalid", f"Maa definition {key} is missing")
    return result
