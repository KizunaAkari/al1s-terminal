"""Map Maa node callbacks to bounded editor-step events."""

import re
from collections.abc import Callable
from typing import Any


def _rule_nodes(pipeline: dict[str, Any]) -> dict[str, tuple[str, int, bool, str | None]]:
    nodes: dict[str, tuple[str, int, bool, str | None]] = {}
    pending = [pipeline]
    while pending:
        values = pending.pop()
        for name, node in values.items():
            if not isinstance(node, dict):
                continue
            pending.append(node)
            metadata = node.get("attach", {})
            if not isinstance(metadata, dict):
                continue
            key = metadata.get("popup_key")
            match = re.search(r"_Global_(\d+)_ForStep_\d+$", key) if isinstance(key, str) else None
            if isinstance(key, str) and match and 0 <= int(match[1]) < 50:
                module_key = metadata.get("definition_key")
                nodes[name] = (
                    key,
                    int(match[1]),
                    False,
                    module_key if isinstance(module_key, str) else None,
                )
    return {
        name: (
            key,
            index,
            name == key + "_Click" or (name == key and key + "_Click" not in nodes),
            module,
        )
        for name, (key, index, _, module) in nodes.items()
    }


def install_step_sink(
    tasker: Any,
    node_steps: dict[str, int],
    step_exits: dict[int, list[str]],
    emit: Callable[[str, int], None],
    *,
    pipeline: dict[str, Any] | None = None,
) -> object:
    from maa.context import ContextEventSink

    exits = {name for names in step_exits.values() for name in names}
    rules = _rule_nodes(pipeline or {})

    class StepSink(ContextEventSink):  # type: ignore[misc]  # Maa ships no typing for this base.
        def __init__(self) -> None:
            self.active_step: int | None = None
            self.active_rule: str | None = None
            self.waiting_parent: str | None = None
            self.count = 0

        def step_event(self, kind: str, step: int) -> None:
            if self.count >= 999:
                return
            self.count += 1
            self.active_step = step if kind == "step_started" else None
            emit(kind, step + 1)

        def start_step(self, step: int) -> None:
            if step != self.active_step:
                self.step_event("step_started", step)

        def next_list(self, details: dict[str, Any]) -> None:
            # PipelineNode.name is the *parent* whose next list is being polled.
            # Start the waiting step even when recognition has not hit yet.
            for candidate in details.get("list", []):
                name = candidate.get("name") if isinstance(candidate, dict) else candidate
                if (
                    isinstance(name, str)
                    and (pipeline or {}).get(name, {}).get("attach", {}).get("dsl_action")
                    == "failure_skip"
                ):
                    continue
                if isinstance(name, str) and name not in rules and name in node_steps:
                    self.waiting_parent = details.get("name")
                    self.start_step(node_steps[name])
                    return

        def rule_event(self, message: str, rule: tuple[str, int, bool, str | None]) -> None:
            key, index, terminal, module = rule
            kind = None
            if message == "Node.Action.Starting" and self.active_rule != key:
                kind, self.active_rule = "rule_started", key
            elif message == "Node.Action.Succeeded" and terminal and self.active_rule == key:
                kind, self.active_rule = "rule_succeeded", None
            elif (
                message in {"Node.Action.Failed", "Node.PipelineNode.Failed"}
                and self.active_rule == key
            ):
                kind, self.active_rule = "rule_failed", None
            if kind:
                self.count += 1
                emit(f"{kind}:{module}" if module else kind, index)

        def on_raw_notification(
            self, _context: object, message: str, details: dict[str, Any]
        ) -> None:
            if self.count >= 999:
                return
            if message == "Node.NextList.Starting":
                self.next_list(details)
                return
            name = details.get("name")
            if not isinstance(name, str):
                return
            if name in rules:
                self.rule_event(message, rules[name])
                return
            if message == "Node.PipelineNode.Failed":
                # An action failure already emitted its event using the actual
                # node. Only a no-match timeout needs the pending step here.
                actual = details.get("node_details", {}).get("name")
                if (
                    not actual and name == self.waiting_parent
                    and self.active_rule is None and self.active_step is not None
                ):
                    self.step_event("step_failed", self.active_step)
                return
            if not message.startswith("Node.Action."):
                return
            step = node_steps.get(name)
            if step is None:
                return
            if (pipeline or {}).get(name, {}).get("attach", {}).get("dsl_action") == "failure_skip":
                return
            if message.endswith(".Starting"):
                self.start_step(step)
            elif message.endswith(".Succeeded") and name in exits and self.active_step == step:
                self.step_event("step_succeeded", step)
            elif message.endswith(".Failed"):
                self.step_event("step_failed", step)

    sink = StepSink()
    if tasker.add_context_sink(sink) is None:
        raise RuntimeError("Maa node event sink is unavailable")
    return sink
