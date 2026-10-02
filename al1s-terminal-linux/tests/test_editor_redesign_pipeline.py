from __future__ import annotations

import json
from types import SimpleNamespace

from al1s_terminal.execution.custom_extensions import register_custom_extensions
from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler


def test_system_task_view_and_random_wait_compile(tmp_path) -> None:
    compiled = MaaPipelineCompiler(tmp_path).compile(
        {
            "steps": [
                {"action": "task_view"},
                {"action": "wait_random", "min_seconds": 1, "max_seconds": 2, "timeout_seconds": 5},
            ]
        }
    )
    key_wrapper = compiled.pipeline[compiled.step_nodes[0][0]]
    wait_wrapper = compiled.pipeline[compiled.step_nodes[1][0]]
    key = compiled.pipeline[key_wrapper["custom_action_param"]["source"]]
    wait = compiled.pipeline[wait_wrapper["custom_action_param"]["source"]]
    assert key["action"] == "ClickKey" and key["key"] == 187
    assert wait["custom_action"] == MaaPipelineCompiler.RANDOM_WAIT_ACTION


def test_recognize_text_then_repeat_fixed_swipe(tmp_path) -> None:
    compiled = MaaPipelineCompiler(tmp_path).compile(
        {
            "target": {"screen_size": {"width": 1080, "height": 1920}},
            "steps": [
                {
                    "action": "recognize_execute",
                    "recognition_mode": "text",
                    "text": "领取[奖励]",
                    "execution_mode": "fixed_swipe",
                    "swipe": {"x1": 10, "y1": 20, "x2": 30, "y2": 40, "duration_ms": 350},
                    "execution_count": 3,
                    "execution_interval_ms": 100,
                }
            ],
        }
    )
    assert compiled.requires_ocr
    node = compiled.pipeline[compiled.step_nodes[0][0]]
    assert node["action"] == "Custom"
    repeated = compiled.pipeline[node["custom_action_param"]["source"]]
    assert repeated["custom_action_param"]["count"] == "3"
    swipe = compiled.pipeline[repeated["custom_action_param"]["source"]]
    assert swipe["action"] == "Swipe"
    assert swipe["begin"] == [10, 20] and swipe["end"] == [30, 40]


def test_ocr_then_image_center_recognizes_before_repeated_clicks(tmp_path) -> None:
    compiled = MaaPipelineCompiler(tmp_path).compile(
        {
            "target": {"screen_size": {"width": 1080, "height": 1920}},
            "steps": [
                {
                    "action": "recognize_execute",
                    "recognition_mode": "text",
                    "text": "领取",
                    "execution_mode": "image_center",
                    "click_template_base64": "data:image/png;base64,AQ==",
                    "execution_count": 3,
                }
            ],
        }
    )
    recognize_name, click_name = compiled.step_nodes[0]
    recognize = compiled.pipeline[recognize_name]
    click = compiled.pipeline[click_name]
    assert recognize["next"] == [click_name]
    assert recognize["custom_recognition"] == "Al1sStepBudgetRecognition"
    assert click["custom_recognition"] == "Al1sStepBudgetRecognition"
    click_source = compiled.pipeline[click["custom_action_param"]["source"]]
    assert click_source["custom_action"] == "Al1sRepeatedClick"
    assert click_source["custom_action_param"]["count"] == "3"
    repeated_action = compiled.pipeline[click_source["custom_action_param"]["source"]]
    assert repeated_action["action"] == "Click"
    assert repeated_action["target"] is True


def test_random_wait_samples_once_per_invocation(monkeypatch) -> None:
    from al1s_terminal.execution import custom_extensions

    class Resource:
        def __init__(self) -> None:
            self.actions: dict = {}

        def register_custom_action(self, name, action):
            self.actions[name] = action
            return True

        def register_custom_recognition(self, _name, _recognition):
            return True

    monkeypatch.setattr(custom_extensions.random, "uniform", lambda low, high: 0.0)
    resource = Resource()
    collector = {
        key: []
        for key in (
            "custom_actions",
            "feedback",
            "conditional_skip_captures",
            "numeric_conditions",
            "yolo",
            "color_markers",
        )
    }
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
        device=SimpleNamespace(screenshot=lambda: b"png"),
        yolo=SimpleNamespace(),
        maa=maa,
        parse_json=json.loads,
        first_number=lambda _: None,
        rect_tuple=lambda _: (0, 0, 1, 1),
        find_color_markers=lambda *_: [],
    )
    action = resource.actions[MaaPipelineCompiler.RANDOM_WAIT_ACTION]
    context = SimpleNamespace(tasker=SimpleNamespace(stopping=False))
    assert action.run(
        context, SimpleNamespace(custom_action_param='{"min_seconds":1,"max_seconds":2}')
    )
