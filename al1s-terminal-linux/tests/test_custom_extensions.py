import json
from types import SimpleNamespace
from typing import Any

from al1s_terminal.execution.custom_extensions import register_custom_extensions
from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler


class _Resource:
    def __init__(self) -> None:
        self.actions: dict[str, Any] = {}
        self.recognitions: dict[str, Any] = {}

    def register_custom_action(self, name: str, action: Any) -> bool:
        self.actions[name] = action
        return True

    def register_custom_recognition(self, name: str, recognition: Any) -> bool:
        self.recognitions[name] = recognition
        return True


def test_extension_registry_keeps_device_and_collector_bound_to_one_run() -> None:
    resource = _Resource()
    collector: dict[str, list[dict[str, Any]]] = {
        name: []
        for name in (
            "custom_actions",
            "numeric_conditions",
            "yolo",
            "color_markers",
            "feedback",
            "conditional_skip_captures",
        )
    }
    device = SimpleNamespace(start_session=lambda: {"session": "started"}, screenshot=lambda: "png")
    maa = {
        name: type(name, (), {})
        for name in (
            "CustomAction",
            "CustomRecognition",
            "JOCR",
            "JRecognitionType",
            "JTemplateMatch",
        )
    }

    register_custom_extensions(
        resource,
        collector,
        device=device,
        yolo=SimpleNamespace(),
        maa=maa,
        parse_json=json.loads,
        first_number=lambda _texts: None,
        rect_tuple=lambda _rect: (0, 0, 1, 1),
        find_color_markers=lambda *_args: [],
    )

    assert len(resource.actions) == 9
    assert len(resource.recognitions) == 4
    assert resource.actions[MaaPipelineCompiler.START_ACTION].run(
        None, SimpleNamespace(node_name="start")
    )
    assert collector["custom_actions"] == [
        {"node": "start", "action": "start", "result": {"session": "started"}}
    ]
