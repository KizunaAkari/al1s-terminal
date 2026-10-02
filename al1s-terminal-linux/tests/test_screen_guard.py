import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from al1s_terminal.execution.screen_guard import (
    GUARD,
    guard_pipeline,
    register_guard,
    requires_guard,
    screen_size,
)


def test_nested_recovery_also_requires_guard_registration():
    assert requires_guard(
        {"custom_action_param": {"pipeline": {"recognition": {"custom_recognition": GUARD}}}}
    )
    assert not requires_guard({"pipeline": {"recognition": "DirectHit"}})


def test_wraps_recognition_without_changing_action_or_flow():
    pipeline = {
        "click": {
            "recognition": "TemplateMatch",
            "template": "a.png",
            "action": "Click",
            "target": [1, 2, 3, 4],
            "next": ["done"],
        },
        "done": {"recognition": "DirectHit", "action": "DoNothing"},
    }
    original = copy.deepcopy(pipeline)
    guard_pipeline(pipeline, (1080, 2400))
    assert pipeline["click"]["custom_recognition"] == GUARD
    assert pipeline["click"]["action"] == "Click"
    assert pipeline["click"]["next"] == ["done"]
    assert pipeline["click_ScreenSource"]["recognition"] == "TemplateMatch"
    assert pipeline["click_ScreenSource"]["action"] == "DoNothing"
    assert pipeline["done"] == original["done"]
    wrapped = copy.deepcopy(pipeline)
    guard_pipeline(pipeline, (1080, 2400))
    assert pipeline == wrapped


def test_legacy_pipeline_is_unchanged():
    pipeline = {"click": {"recognition": "DirectHit", "action": "Click"}}
    original = copy.deepcopy(pipeline)
    guard_pipeline(pipeline, None)
    assert pipeline == original
    assert screen_size({}) is None
    assert screen_size({"target": {"screen_size": {"width": 1080, "height": 2400}}}) == (1080, 2400)


def test_launch_is_not_blocked_by_desktop_orientation_and_waits_before_recognition():
    pipeline = {
        "start": {
            "recognition": "DirectHit",
            "action": "Custom",
            "custom_action": "MaaProjectStart",
        },
        "stop": {"recognition": "DirectHit", "action": "StopApp", "next": ["launch"]},
        "launch": {"recognition": "DirectHit", "action": "StartApp", "next": ["image"]},
        "image": {"recognition": "TemplateMatch", "action": "Click"},
    }
    guard_pipeline(pipeline, (2400, 1080))
    assert pipeline["start"]["recognition"] == "DirectHit"
    assert pipeline["stop"]["recognition"] == "DirectHit"
    assert pipeline["launch"]["recognition"] == "DirectHit"
    assert pipeline["launch"]["next"] == ["launch_OrientationReady"]
    assert pipeline["launch_OrientationReady"]["next"] == ["image"]
    assert pipeline["image"]["custom_recognition"] == GUARD
    original = copy.deepcopy(pipeline)
    guard_pipeline(pipeline, (2400, 1080))
    assert pipeline == original


def test_orientation_wait_is_bounded_and_popup_mismatch_is_only_a_miss(monkeypatch):
    from al1s_terminal.execution import screen_guard

    resource, failures = Mock(), []
    register_guard(resource, type("Recognition", (), {"AnalyzeResult": SimpleNamespace}), failures)
    callbacks = {
        call.args[0]: call.args[1] for call in resource.register_custom_recognition.call_args_list
    }
    now = [0.0]
    monkeypatch.setattr(screen_guard.time, "monotonic", lambda: now[0])
    args = SimpleNamespace(
        image=SimpleNamespace(shape=(2400, 1080, 3)),
        custom_recognition_param=json.dumps({"width": 2400, "height": 1080, "key": "ready"}),
    )
    ready = callbacks[screen_guard.READY]
    assert ready.analyze(Mock(), args) is None and not failures
    args.image.shape = (1080, 2400, 3)
    assert ready.analyze(Mock(), args).detail == {"ready": True}
    args.image.shape = (2400, 1080, 3)
    assert ready.analyze(Mock(), args) is None
    now[0] = 11
    assert ready.analyze(Mock(), args) is None
    assert failures[0]["actual_width"] == 1080
    failures.clear()
    args.custom_recognition_param = json.dumps(
        {"width": 2400, "height": 1080, "source": "popup", "optional_popup": True}
    )
    context = Mock()
    assert callbacks[GUARD].analyze(context, args) is None and not failures
    context.run_recognition.assert_not_called()


@pytest.mark.parametrize(
    "size",
    [
        {"width": True, "height": 100},
        {"width": 0, "height": 2},
        {"width": 8192, "height": 8192},
        {"width": 100, "height": 100, "blob": "bad"},
        None,
    ],
)
def test_rejects_invalid_size(size):
    with pytest.raises(ValueError):
        screen_size({"target": {"screen_size": size}})


def test_guard_reuses_frame_and_latches_mismatch_before_original_recognition():
    resource = Mock()

    class Recognition:
        AnalyzeResult = SimpleNamespace

    failures = []
    register_guard(resource, Recognition, failures)
    callback = next(
        call.args[1]
        for call in resource.register_custom_recognition.call_args_list
        if call.args[0] == GUARD
    )
    frame = SimpleNamespace(shape=(2400, 1080, 3))
    args = SimpleNamespace(
        image=frame,
        custom_recognition_param=json.dumps(
            {"width": 1080, "height": 2400, "source": "click_ScreenSource"}
        ),
    )
    context = Mock()
    context.run_recognition.return_value = SimpleNamespace(
        hit=True, box=[10, 20, 30, 40], raw_detail={"ok": True}
    )
    assert callback.analyze(context, args).box == (10, 20, 30, 40)
    context.run_recognition.assert_called_once_with("click_ScreenSource", frame)
    frame.shape = (1080, 2400, 3)
    assert callback.analyze(context, args) is None
    frame.shape = (2400, 1080, 3)
    assert callback.analyze(context, args) is None
    assert context.run_recognition.call_count == 1
    assert len(failures) == 1
    context.tasker.post_stop.assert_not_called()
