"""Delegate only already-registered actions without losing native recognition.

Maa 5.12 run_action invokes Custom with reco_id=0; its Python bridge then
rejects the callback. Invoke our trusted registered Python handler directly
instead, retaining the outer recognition and avoiding another screenshot.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any


class RegisteredActions:
    def __init__(self, resource: Any):
        self.resource = resource
        self.actions: dict[str, Any] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self.resource, name)

    def register_custom_action(self, name: str, handler: Any) -> bool:
        if not self.resource.register_custom_action(name, handler):
            return False
        self.actions[name] = handler
        return True

    def invoke(self, context: Any, argv: Any, source: str) -> Any:
        node = context.get_node_data(source)
        if not isinstance(node, dict):
            raise RuntimeError("Delegated Maa action is unavailable")
        action = node.get("action")
        kind = action.get("type") if isinstance(action, dict) else action
        params = action.get("param", {}) if isinstance(action, dict) else node
        if kind == "Custom":
            name = params.get("custom_action")
            if not isinstance(name, str):
                raise RuntimeError("Delegated custom action name is invalid")
            handler = self.actions.get(name)
            if handler is None:
                raise RuntimeError("Delegated custom action is not registered")
            value = params.get("custom_action_param", {})
            argument = SimpleNamespace(
                task_detail=argv.task_detail,
                node_name=argv.node_name,
                custom_action_name=name,
                custom_action_param=value if isinstance(value, str) else json.dumps(value),
                reco_detail=argv.reco_detail,
                box=argv.box,
            )
            result = handler.run(context, argument)
            success = (
                result if isinstance(result, bool) else True if result is None else result.success
            )
            return SimpleNamespace(success=success)
        metadata = node.get("attach", {})
        center = (
            kind == "Click"
            and isinstance(metadata, dict)
            and metadata.get("click_match_center") is True
        )
        return native_action(context, argv, source, center=center)


def native_action(context: Any, argv: Any, source: str, *, center: bool = False) -> Any:
    box = argv.box
    rect = (box.x, box.y, box.w, box.h) if hasattr(box, "x") else tuple(box)
    if center:
        x, y, width, height = rect
        if width < 1 or height < 1:
            raise RuntimeError("Matched image box is empty")
        rect = (x + (width - 1) // 2, y + (height - 1) // 2, 1, 1)
    return context.run_action(source, rect, json.dumps(argv.reco_detail.raw_detail))


def delegated_action(resource: Any, context: Any, argv: Any, source: str) -> Any:
    if isinstance(resource, RegisteredActions):
        return resource.invoke(context, argv, source)
    return native_action(context, argv, source)
