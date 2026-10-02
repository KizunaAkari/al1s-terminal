"""Real pinned Maa callbacks, with an in-memory controller (no phone operations)."""

# Maa imports must follow importorskip so offline environments skip this native test.
# ruff: noqa: E402

import pytest

pytest.importorskip("maa")
pytestmark = pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning")
import numpy as np
from maa.controller import CustomController
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition
from maa.resource import Resource
from maa.tasker import Tasker
from maa.toolkit import Toolkit

from al1s_terminal.execution.action_dispatch import RegisteredActions
from al1s_terminal.execution.popup_guard import register_popup_guard, wrap_popup_guards
from al1s_terminal.execution.repeated_click import register_repeated_click, wrap_repeated_clicks
from al1s_terminal.execution.screen_guard import guard_pipeline, register_guard, wait_guarded
from al1s_terminal.execution.step_budget import register_step_budgets, wrap_step_budgets


class MemoryController(CustomController):
    def __init__(self, rotate):
        super().__init__()
        self.rotate = rotate
        self.clicks = []

    def connect(self):
        return True

    def request_uuid(self):
        return "screen-guard-test"

    def get_features(self):
        return 0

    def screencap(self):
        shape = (64, 96, 3) if self.rotate and self.clicks else (96, 64, 3)
        return np.zeros(shape, dtype=np.uint8)

    def click(self, x, y):
        self.clicks.append((x, y))
        return True

    def unsupported(self, *args):
        return False

    start_app = stop_app = swipe = touch_down = touch_move = touch_up = unsupported
    click_key = input_text = key_down = key_up = unsupported


def test_native_launch_from_portrait_waits_for_landscape_before_click(tmp_path):
    class LaunchController(MemoryController):
        launched = False
        frames = 0

        def start_app(self, package):
            self.launched = True
            return True

        def stop_app(self, package):
            self.launched = False
            return True

        def screencap(self):
            if self.launched:
                self.frames += 1
            return np.zeros((64, 96, 3) if self.frames >= 3 else (96, 64, 3), dtype=np.uint8)

    Toolkit.init_option(tmp_path)
    controller = LaunchController(False)
    controller.set_screenshot_use_raw_size(True)
    assert controller.post_connection().wait().succeeded
    resource, tasker, failures = Resource(), Tasker(), []
    register_guard(resource, CustomRecognition, failures)
    assert tasker.bind(resource, controller)
    pipeline = {
        "stop": {
            "recognition": "DirectHit",
            "action": "StopApp",
            "package": "test",
            "next": ["launch"],
        },
        "launch": {
            "recognition": "DirectHit",
            "action": "StartApp",
            "package": "test",
            "next": ["click"],
        },
        "click": {"recognition": "DirectHit", "action": "Click", "target": [10, 20]},
    }
    guard_pipeline(pipeline, (96, 64))
    job = wait_guarded(tasker, tasker.post_task("stop", pipeline), failures)
    assert job.succeeded and not failures
    assert controller.launched and controller.frames >= 3
    assert len(controller.clicks) == 1


@pytest.mark.parametrize("scenario", ["paused", "timeout_recovery", "wait_timeout", "rule_timeout"])
def test_native_step_clock_and_rule_clock(tmp_path, scenario):
    Toolkit.init_option(tmp_path)
    controller = MemoryController(False)
    controller.set_screenshot_use_raw_size(True)
    assert controller.post_connection().wait().succeeded
    resource, tasker, failures = Resource(), Tasker(), []

    class Never(CustomRecognition):
        def analyze(self, context, argv):
            return None

    class Once(CustomRecognition):
        def analyze(self, context, argv):
            return None if controller.clicks else self.AnalyzeResult(box=(10, 20, 1, 1), detail={})

    resource.register_custom_recognition("Never", Never())
    resource.register_custom_recognition("Once", Once())
    registrations = RegisteredActions(resource)
    register_popup_guard(registrations, CustomRecognition, CustomAction, failures)
    register_step_budgets(registrations, CustomRecognition, CustomAction, failures)
    assert tasker.bind(resource, controller)
    pipeline = {
        "root": {
            "recognition": "DirectHit",
            "action": "DoNothing",
            "post_delay": 0,
            "next": ["main"],
            "timeout": 100,
            "rate_limit": 10,
        },
        "main": {
            "recognition": "DirectHit",
            "action": "DoNothing",
            "post_delay": 0,
            "attach": {"dsl_step_index": 0},
            "on_error": ["recovered"] if scenario == "timeout_recovery" else [],
        },
        "recovered": {"recognition": "DirectHit", "action": "DoNothing", "post_delay": 0},
    }
    if scenario in {"paused", "rule_timeout"}:
        pipeline["root"]["next"].insert(0, {"name": "popup", "jump_back": True})
        pipeline["popup"] = {
            "recognition": "Custom",
            "custom_recognition": "Once",
            "action": "Click",
            "target": [10, 20],
            "post_delay": 250,
            "pre_delay": 0,
            "attach": {
                "dsl_step_index": 0,
                "popup_key": "rule",
                "popup_condition": True,
                "popup_budget": 0.1 if scenario == "rule_timeout" else 2,
            },
        }
    elif scenario == "timeout_recovery":
        pipeline["main"].update(
            recognition="Custom", custom_recognition="Never", action="Click", target=[10, 20]
        )
    else:
        pipeline["main"]["post_delay"] = 1000
    wrap_popup_guards(pipeline)
    wrap_step_budgets(pipeline, [{"timeout_seconds": 0.1}], "test")
    job = wait_guarded(tasker, tasker.post_task("root", pipeline), failures)
    if scenario in {"paused", "timeout_recovery"}:
        assert job.succeeded and not failures, (failures, controller.clicks, job.get())
        assert len(controller.clicks) == (1 if scenario == "paused" else 0)
    elif scenario == "rule_timeout":
        assert failures and failures[0]["error_type"] == "MaaIndependentRuleTimeout", (
            failures,
            controller.clicks,
            job.get(),
        )
    else:
        assert not job.succeeded


@pytest.mark.parametrize("clears", [False, True])
@pytest.mark.parametrize("repeat", [1, 6])
def test_native_popup_limit_rechecks_after_ten_successful_clicks(tmp_path, clears, repeat):
    Toolkit.init_option(tmp_path)
    controller = MemoryController(False)
    controller.set_screenshot_use_raw_size(True)
    assert controller.post_connection().wait().succeeded
    resource, tasker, failures = Resource(), Tasker(), []

    class Popup(CustomRecognition):
        def analyze(self, context, argv):
            if clears and len(controller.clicks) >= 10:
                return None
            return self.AnalyzeResult(box=(10, 20, 1, 1), detail={})

    assert resource.register_custom_recognition("TestPopup", Popup())
    registrations = RegisteredActions(resource)
    clock = register_step_budgets(registrations, CustomRecognition, CustomAction, failures)
    register_popup_guard(registrations, CustomRecognition, CustomAction, failures)
    register_repeated_click(registrations, CustomAction, failures, clock)
    register_guard(resource, CustomRecognition, failures)
    assert tasker.bind(resource, controller)
    pipeline = {
        "root": {
            "recognition": "DirectHit",
            "action": "DoNothing",
            "post_delay": 0,
            "next": [{"name": "popup", "jump_back": True}, "end"],
            "timeout": 3000,
            "rate_limit": 0,
        },
        "popup": {
            "recognition": "Custom",
            "custom_recognition": "TestPopup",
            "repeat": repeat,
            "repeat_delay": 0,
            "action": "Click",
            "target": [10, 20],
            "pre_delay": 0,
            "post_delay": 0,
            "attach": {"popup_key": "step0-rule0", "popup_condition": True, "dsl_step_index": 0},
        },
        "end": {"recognition": "DirectHit", "action": "DoNothing", "post_delay": 0},
    }
    wrap_repeated_clicks(pipeline, [{"timeout_seconds": 10}], (64, 96))
    wrap_popup_guards(pipeline)
    guard_pipeline(pipeline, (64, 96))
    wrap_step_budgets(pipeline, [{"timeout_seconds": 10}], "popup-test")
    job = wait_guarded(tasker, tasker.post_task("root", pipeline), failures)
    assert len(controller.clicks) == 10
    assert bool(failures) is not clears
    if clears:
        assert job.succeeded
    else:
        assert failures[0]["error_type"] == "MaaIndependentRuleLimit"


@pytest.mark.parametrize("rotate,expected_clicks", [(False, 2), (True, 1)])
def test_native_guard_preserves_clicks_and_stops_on_rotation(tmp_path, rotate, expected_clicks):
    Toolkit.init_option(tmp_path)
    controller = MemoryController(rotate)
    controller.set_screenshot_use_raw_size(True)
    assert controller.post_connection().wait().succeeded
    resource = Resource()
    failures = []
    register_guard(resource, CustomRecognition, failures)
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    pipeline = {
        "first": {
            "recognition": "DirectHit",
            "action": "Click",
            "target": [10, 20],
            "pre_delay": 0,
            "post_delay": 0,
            "next": ["second"],
            "timeout": 1000,
        },
        "second": {
            "recognition": "DirectHit",
            "action": "Click",
            "target": [11, 21],
            "pre_delay": 0,
            "post_delay": 0,
        },
    }
    guard_pipeline(pipeline, (64, 96))
    job = wait_guarded(tasker, tasker.post_task("first", pipeline), failures)
    assert len(controller.clicks) == expected_clicks
    assert bool(failures) is rotate
    if not rotate:
        assert job.succeeded


@pytest.mark.parametrize("rotate,timeout", [(False, False), (True, False), (False, True)])
def test_native_repeated_clicks_are_not_truncated_and_stop_safely(tmp_path, rotate, timeout):
    Toolkit.init_option(tmp_path)
    controller = MemoryController(rotate)
    controller.set_screenshot_use_raw_size(True)
    assert controller.post_connection().wait().succeeded
    resource = Resource()
    failures = []
    register_guard(resource, CustomRecognition, failures)
    register_repeated_click(resource, CustomAction, failures)
    tasker = Tasker()
    assert tasker.bind(resource, controller)
    pipeline = {
        "click": {
            "recognition": "DirectHit",
            "action": "Click",
            "target": [10, 20],
            "pre_delay": 0,
            "post_delay": 0,
            "repeat": 25,
            "repeat_delay": 200 if timeout else 0,
            "attach": {"dsl_step_index": 0},
        }
    }
    wrap_repeated_clicks(pipeline, [{"timeout_seconds": 0.1 if timeout else 20}], (64, 96))
    guard_pipeline(pipeline, (64, 96))
    job = wait_guarded(tasker, tasker.post_task("click", pipeline), failures)
    if rotate:
        assert len(controller.clicks) == 1
        assert failures[0]["error_type"] == "MaaScreenSizeMismatch"
    elif timeout:
        assert len(controller.clicks) < 25
        assert failures[0]["error_type"] == "MaaClickRepeatTimeout"
    else:
        assert job.succeeded
        assert len(controller.clicks) == 25
        assert not failures
