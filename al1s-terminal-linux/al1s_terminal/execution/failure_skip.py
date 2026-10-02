"""Failure-only step exits, scoped to the actual native failure owner."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

RECOGNITION = "Al1sFailureSkipRecognition"
ACTION = "Al1sFailureSkip"
REASONS = frozenset({"step_timeout", "action_failed", "recovery_failed", "retry_exhausted"})
FAILURE_MODES = frozenset({"recognition_failure", "execution_failure"})


def wire_failure_skips(
    pipeline: dict[str, dict[str, Any]],
    steps: list[Any],
    prefix: str,
    entries: dict[int, list[Any]],
    node_steps: dict[str, int],
    step_nodes: dict[int, list[str]],
) -> None:
    guards: dict[int, str] = {}
    for index, step in enumerate(steps):
        config = step.get("skip_condition", {})
        if (
            not isinstance(config, dict)
            or config.get("enabled") is not True
            or config.get("mode") not in FAILURE_MODES
        ):
            continue
        if step.get("action") == "start":
            raise ValueError("Start step cannot be skipped")
        explicit_target = config.get("skip_to_step_index")
        target = index + 2 if explicit_target is None else explicit_target
        last_target = len(steps) + (explicit_target is None and index == len(steps) - 1)
        if type(target) is not int or not index + 2 <= target <= last_target:
            raise ValueError("Skip target must be a later step")
        name = f"{prefix}_Step_{index:03d}_SkipIfFailure"
        parameters = {
            "step_index": index,
            "target_index": target - 1,
            "phase": str(config["mode"]).removesuffix("_failure"),
            "scope": prefix,
        }
        pipeline[name] = {
            "recognition": "Custom",
            "custom_recognition": RECOGNITION,
            "custom_recognition_param": parameters,
            "action": "Custom",
            "custom_action": ACTION,
            "custom_action_param": parameters,
            "next": list(entries[target - 1]),
            "timeout": -1,
            "rate_limit": 100,
            "attach": {
                "dsl_step_index": index,
                "dsl_action": "failure_skip",
                "maa_project_role": "failure-skip",
            },
        }
        node_steps[name] = index
        step_nodes[index].append(name)
        guards[index] = name
    if not guards:
        return
    reject = prefix + "_FailureSkipReject"
    pipeline[reject] = {
        "recognition": "DirectHit",
        "action": "Custom",
        "custom_action": ACTION,
        "custom_action_param": {"step_index": -1, "phase": "none", "scope": "none"},
        "next": [],
    }
    # Existing recovery candidates stay first. The guarded fallback recognizers
    # only match the step that actually failed, including next-list timeouts.
    guard_names = set(guards.values())
    for name, node in pipeline.items():
        if name in guard_names:
            continue
        owners = {node_steps.get(name)}
        for target in node.get("next", []):
            target_name = target.get("name") if isinstance(target, dict) else target
            if isinstance(target_name, str):
                owners.add(node_steps.get(target_name))
        fallbacks = [
            guards[index] for index in sorted(index for index in owners if index in guards)
        ]
        if not fallbacks:
            continue
        candidates = node.setdefault("on_error", [])
        for guard in fallbacks:
            if guard not in candidates:
                candidates.append(guard)
        candidates.append(reject)


class FailureState:
    def __init__(self, pipeline: dict[str, dict[str, Any]], node_steps: dict[str, int], clock: Any):
        self.pipeline, self.node_steps, self.clock = dict(pipeline), dict(node_steps), clock
        pending = list(pipeline.values())
        while pending:
            node = pending.pop()
            child = node.get("custom_action_param", {}).get("pipeline", {})
            for name, nested in child.items():
                if name in self.pipeline:
                    continue
                self.pipeline[name] = nested
                pending.append(nested)
                index = nested.get("attach", {}).get("dsl_step_index")
                if type(index) is int and not name.endswith("Source"):
                    self.node_steps[name] = index
        self.pending: tuple[str, int, str] | None = None
        self.failure: dict[str, Any] | None = None
        self.misses: set[tuple[str, int]] = set()
        self.skip_phases = {
            (node["custom_action_param"]["scope"], node["custom_action_param"]["step_index"]): node[
                "custom_action_param"
            ]["phase"]
            for node in self.pipeline.values()
            if node.get("custom_recognition") == RECOGNITION
        }

    def observe(self, message: str, details: dict[str, Any]) -> None:
        name = details.get("name")
        node = self.pipeline.get(name, {}) if isinstance(name, str) else {}
        metadata = node.get("attach", {})
        if message in {"Node.Recognition.Failed", "Node.Recognition.Succeeded"}:
            index = metadata.get("dsl_step_index")
            if (
                type(index) is int
                and not metadata.get("popup_key")
                and node.get("recognition") in {"TemplateMatch", "OCR"}
            ):
                if message.endswith(".Failed"):
                    self.misses.add((self._scope(name), index))
                else:
                    self.misses.discard((self._scope(name), index))
        elif message == "Node.NextList.Starting":
            for candidate in details.get("list", []):
                key = candidate.get("name") if isinstance(candidate, dict) else candidate
                if key in self.node_steps and not self._is_skip(key):
                    self.pending = (str(name), self.node_steps[key], self._scope(key))
                    self._start_next_budget(key)
                    break
        elif (
            message == "Node.Action.Failed" and name in self.node_steps and not self._is_skip(name)
        ):
            self._action_failure(name)
        elif (
            message == "Node.PipelineNode.Failed"
            and self.pending
            and name == self.pending[0]
            and not details.get("node_details", {}).get("name")
        ):
            self.failure = {
                "step_index": self.pending[1],
                "node": name,
                "reason": "step_timeout",
                "phase": "recognition",
                "scope": self.pending[2],
            }
            self._release_failed_budget()

    def _action_failure(self, name: str) -> None:
        metadata = self.pipeline[name].get("attach", {})
        if metadata.get("popup_key"):
            return
        reason = {
            "failure_retry_process": "recovery_failed",
            "failure_retry_limit": "retry_exhausted",
        }.get(metadata.get("dsl_action"), "action_failed")
        index = self.node_steps[name]
        phase = (
            "recognition"
            if (self._scope(name), index) in self.misses and self._algorithm(name) != "DirectHit"
            else "execution"
        )
        if reason == "retry_exhausted" and self.failure and self.failure["step_index"] == index:
            phase = self.failure["phase"]
        if (
            reason == "action_failed"
            and self.clock is not None
            and self.clock.rule is None
            and self.clock.remaining() <= 0
        ):
            reason = "step_timeout"
        self.failure = {
            "step_index": index,
            "node": name,
            "reason": reason,
            "phase": phase,
            "scope": self._scope(name),
        }
        self._release_failed_budget()

    def _release_failed_budget(self) -> None:
        failure = self.failure
        if failure is None or self.clock is None:
            return
        key = (failure["scope"], failure["step_index"])
        if self.skip_phases.get(key) == failure["phase"]:
            self.clock.finish_failed_step(f"{key[0]}:{key[1]}")

    def _start_next_budget(self, name: str) -> None:
        if self.clock is None or self.clock.active is not None:
            return
        node = self.pipeline[name]
        if node.get("custom_recognition") != "Al1sStepBudgetRecognition":
            return
        config = node["custom_recognition_param"]
        self.clock.enter(config["key"], config.get("rule"))
        self.clock.budget = config["budget"]
        self.clock.rule_budget = config.get("rule_budget", 30)

    def _is_skip(self, name: str) -> bool:
        return self.pipeline.get(name, {}).get("custom_action") == ACTION

    @staticmethod
    def _scope(name: object) -> str:
        return (
            name.split("_Step_", 1)[0] if isinstance(name, str) and name.startswith("Web_") else ""
        )

    def _algorithm(self, name: str) -> Any:
        visited = set()
        while name not in visited:
            visited.add(name)
            node = self.pipeline.get(name, {})
            source = node.get("custom_recognition_param", {}).get("source")
            if not isinstance(source, str):
                return node.get("recognition")
            name = source
        return None


def register_failure_skips(
    resource: Any,
    tasker: Any,
    maa: dict[str, Any],
    compiled: Any,
    collector: dict[str, list[dict[str, Any]]],
    failures: list[dict[str, Any]],
    clock: Any,
    capture: Callable[[], Any],
    emit: Callable[[str, int], None] | None,
) -> FailureState | None:
    state = FailureState(compiled.pipeline, compiled.node_steps, clock)
    if not any(node.get("custom_action") == ACTION for node in state.pipeline.values()):
        return None
    from maa.context import ContextEventSink

    recognition_base: Any = maa["CustomRecognition"]
    action_base: Any = maa["CustomAction"]

    def accepted(context: Any, parameters: str) -> dict[str, Any] | None:
        config = json.loads(parameters)
        index = config["step_index"]
        failure = state.failure
        return (
            failure
            if not failures
            and not context.tasker.stopping
            and failure
            and failure["step_index"] == index
            and failure["phase"] == config["phase"]
            and failure["scope"] == config["scope"]
            else None
        )

    class Recognition(recognition_base):  # type: ignore[misc]  # Native callback base.
        def analyze(self, context: Any, argv: Any) -> Any:
            failure = accepted(context, argv.custom_recognition_param)
            return self.AnalyzeResult(box=(0, 0, 1, 1), detail=failure) if failure else None

    class Action(action_base):  # type: ignore[misc]  # Native callback base.
        def run(self, context: Any, argv: Any) -> bool:
            failure = accepted(context, argv.custom_action_param)
            if failure is None:
                return False
            evidence = capture()
            if failures or context.tasker.stopping:
                return False
            collector["conditional_skip_captures"].append(
                {
                    "node": argv.node_name,
                    "step_index": failure["step_index"],
                    "mode": failure["phase"] + "_failure",
                    "failure_reason": failure["reason"],
                    "failed_node": failure["node"],
                    "capture": evidence,
                }
            )
            if emit and failure["scope"] == "Web_" + compiled.script_hash:
                emit(
                    "failure_skipped:" + failure["phase"] + ":" + failure["reason"],
                    failure["step_index"] + 1,
                )
            state.failure = None
            return True

    class Sink(ContextEventSink):  # type: ignore[misc]  # Native callback base.
        def on_raw_notification(self, _context: Any, message: str, details: dict[str, Any]) -> None:
            state.observe(message, details)

    if not resource.register_custom_recognition(RECOGNITION, Recognition()):
        raise RuntimeError("Failure skip recognition registration failed")
    if not resource.register_custom_action(ACTION, Action()):
        raise RuntimeError("Failure skip action registration failed")
    if tasker.add_context_sink(Sink()) is None:
        raise RuntimeError("Failure skip event sink registration failed")
    return state
