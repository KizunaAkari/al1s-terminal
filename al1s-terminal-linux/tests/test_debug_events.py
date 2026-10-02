from __future__ import annotations

import sys
from types import ModuleType

import pytest

from al1s_terminal.execution.debug_events import install_step_sink
from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler


def test_native_node_events_map_to_editor_steps_without_raw_details(monkeypatch) -> None:
    maa = ModuleType("maa")
    context = ModuleType("maa.context")
    context.ContextEventSink = type("ContextEventSink", (), {})
    monkeypatch.setitem(sys.modules, "maa", maa)
    monkeypatch.setitem(sys.modules, "maa.context", context)

    class Tasker:
        def add_context_sink(self, sink):
            self.sink = sink
            return 1

    events: list[tuple[str, int]] = []
    tasker = Tasker()
    sink = install_step_sink(
        tasker,
        {"NodeA": 0, "NodeB": 0},
        {0: ["NodeB"]},
        lambda kind, step: events.append((kind, step)),
    )
    for message, name in [
        ("Node.Action.Starting", "NodeA"),
        ("Node.Action.Starting", "NodeB"),
        ("Node.Action.Succeeded", "NodeB"),
    ]:
        sink.on_raw_notification(None, message, {"name": name, "focus": "private"})
    assert events == [("step_started", 1), ("step_succeeded", 1)]


def test_independent_rule_metadata_overrides_a_wrong_main_step_mapping(monkeypatch):
    maa, context = ModuleType("maa"), ModuleType("maa.context")
    context.ContextEventSink = type("ContextEventSink", (), {})
    monkeypatch.setitem(sys.modules, "maa", maa)
    monkeypatch.setitem(sys.modules, "maa.context", context)
    key = "Web_test_Global_000_ForStep_003"
    pipeline = {
        key: {"attach": {"popup_key": key}},
        key + "_Click": {"attach": {"popup_key": key}},
    }
    events = []
    tasker = type("Tasker", (), {"add_context_sink": lambda *_: 1})()
    sink = install_step_sink(
        tasker,
        {key: 2, key + "_Click": 2, "main": 3},
        {2: [key + "_Click"], 3: ["main"]},
        lambda kind, step: events.append((kind, step)),
        pipeline=pipeline,
    )
    for suffix, name in [
        ("Starting", key),
        ("Starting", key + "_Click"),
        ("Succeeded", key + "_Click"),
        ("Starting", key),
        ("Failed", key),
        ("Starting", "main"),
        ("Succeeded", "main"),
    ]:
        sink.on_raw_notification(
            None,
            "Node.Action." + suffix,
            {"name": name},
        )
    sink.on_raw_notification(None, "Node.Recognition.Failed", {"name": key})
    assert events == [
        ("rule_started", 0),
        ("rule_succeeded", 0),
        ("rule_started", 0),
        ("rule_failed", 0),
        ("step_started", 4),
        ("step_succeeded", 4),
    ]


def test_direct_click_rule_has_one_start_and_completion_and_keeps_main_step_active(monkeypatch):
    maa, context = ModuleType("maa"), ModuleType("maa.context")
    context.ContextEventSink = type("ContextEventSink", (), {})
    monkeypatch.setitem(sys.modules, "maa", maa)
    monkeypatch.setitem(sys.modules, "maa.context", context)
    key = "Web_test_Global_001_ForStep_000"
    events = []
    tasker = type("Tasker", (), {"add_context_sink": lambda *_: 1})()
    sink = install_step_sink(
        tasker,
        {"first": 0, "last": 0},
        {0: ["last"]},
        lambda kind, step: events.append((kind, step)),
        pipeline={key: {"attach": {"popup_key": key}}},
    )
    for suffix, name in [
        ("Starting", "first"),
        ("Starting", key),
        ("Succeeded", key),
        ("Starting", "last"),
        ("Succeeded", "last"),
    ]:
        sink.on_raw_notification(
            None,
            "Node.Action." + suffix,
            {"name": name},
        )
    assert events == [
        ("step_started", 1),
        ("rule_started", 1),
        ("rule_succeeded", 1),
        ("step_succeeded", 1),
    ]


@pytest.fixture
def transition_sink(monkeypatch):
    context = ModuleType("maa.context")
    context.ContextEventSink = type("ContextEventSink", (), {})
    monkeypatch.setitem(sys.modules, "maa", ModuleType("maa"))
    monkeypatch.setitem(sys.modules, "maa.context", context)
    key = "Web_test_Global_000_ForStep_003"
    pipeline = {
        "click": {"next": [{"name": key, "jump_back": True}, "wait"]},
        "wait": {"next": ["end"]},
        key: {"attach": {"popup_key": key}},
        key + "_Click": {"attach": {"popup_key": key}},
    }
    events = []
    tasker = type("Tasker", (), {"add_context_sink": lambda *_: 1})()
    sink = install_step_sink(
        tasker, {"click": 2, "wait": 3}, {2: ["click"], 3: ["wait"]},
        lambda kind, step: events.append((kind, step)), pipeline=pipeline,
    )
    return sink, events, key


def test_real_parent_callbacks_do_not_reopen_completed_step_during_two_popups(transition_sink):
    sink, events, key = transition_sink

    def notify(message, name, **details):
        sink.on_raw_notification(None, "Node." + message, {"name": name, **details})

    notify("NextList.Starting", "previous", list=["click"])
    notify("Action.Starting", "click")
    notify("Action.Succeeded", "click")
    for _ in range(2):
        notify("PipelineNode.Starting", "click")
        notify("NextList.Starting", "click", list=[{"name": key, "jump_back": True}, "wait"])
        notify("Action.Starting", key)
        notify("Action.Succeeded", key)
        # The native outer name remains the previous step even though a rule ran.
        notify("PipelineNode.Succeeded", "click", node_details={"name": key})
        notify("NextList.Starting", key, list=[key + "_Click"])
        notify("Action.Starting", key + "_Click")
        notify("Action.Succeeded", key + "_Click")
    notify("PipelineNode.Starting", "click")
    notify("NextList.Starting", "click", list=[{"name": key, "jump_back": True}, "wait"])
    notify("Action.Starting", "wait_BudgetActionSource")
    notify("Action.Succeeded", "wait_BudgetActionSource")
    notify("Action.Starting", "wait")
    notify("Action.Succeeded", "wait")
    notify("PipelineNode.Succeeded", "click", node_details={"name": "wait"})
    notify("NextList.Starting", "wait", list=["end"])
    notify("PipelineNode.Succeeded", "wait", node_details={"name": "end"})
    assert events == [
        ("step_started", 3), ("step_succeeded", 3), ("step_started", 4),
        ("rule_started", 0), ("rule_succeeded", 0),
        ("rule_started", 0), ("rule_succeeded", 0), ("step_succeeded", 4),
    ]


def test_recognition_timeout_belongs_to_waiting_step_not_completed_parent(transition_sink):
    sink, events, _ = transition_sink
    for _ in range(3):
        sink.on_raw_notification(
            None, "Node.NextList.Starting", {"name": "click", "list": ["wait"]}
        )
        sink.on_raw_notification(None, "Node.NextList.Failed", {"name": "click", "list": ["wait"]})
    assert events == [("step_started", 4)]
    sink.on_raw_notification(None, "Node.PipelineNode.Failed", {"name": "click"})
    assert events == [("step_started", 4), ("step_failed", 4)]


@pytest.mark.parametrize("action_failure", [False, True])
def test_rule_failure_never_completes_or_fails_previous_main_step(transition_sink, action_failure):
    sink, events, key = transition_sink
    sink.on_raw_notification(
        None, "Node.NextList.Starting", {"name": "click", "list": [key, "wait"]}
    )
    sink.on_raw_notification(None, "Node.Action.Starting", {"name": key})
    if action_failure:
        sink.on_raw_notification(None, "Node.Action.Failed", {"name": key})
        sink.on_raw_notification(None, "Node.PipelineNode.Failed", {
            "name": "click", "node_details": {"name": key, "completed": False},
        })
    else:
        sink.on_raw_notification(
            None, "Node.NextList.Starting", {"name": key, "list": [key + "_Click"]}
        )
        sink.on_raw_notification(None, "Node.PipelineNode.Failed", {"name": key})
    assert events == [("step_started", 4), ("rule_started", 0), ("rule_failed", 0)]


def test_action_failure_and_parent_failure_emit_once_then_recovery_can_restart(transition_sink):
    sink, events, _ = transition_sink
    for message, details in [
        ("NextList.Starting", {"name": "click", "list": ["wait"]}),
        ("Action.Starting", {"name": "wait"}),
        ("Action.Failed", {"name": "wait"}),
        ("PipelineNode.Failed", {"name": "click", "node_details": {"name": "wait"}}),
        ("NextList.Starting", {"name": "recovered", "list": ["wait"]}),
        ("Action.Starting", {"name": "wait"}),
        ("Action.Succeeded", {"name": "wait"}),
    ]:
        sink.on_raw_notification(None, "Node." + message, details)
    assert events == [
        ("step_started", 4), ("step_failed", 4),
        ("step_started", 4), ("step_succeeded", 4),
    ]


def test_launch_completion_waits_for_orientation_ready(tmp_path, transition_sink):
    compiled = MaaPipelineCompiler(tmp_path).compile({
        "target": {"screen_size": {"width": 96, "height": 64}},
        "steps": [{"action": "launch_app", "package": "test", "force_stop_before_launch": False}],
    })
    events = []
    tasker = type("Tasker", (), {"add_context_sink": lambda *_: 1})()
    sink = install_step_sink(
        tasker, compiled.node_steps, compiled.step_exits,
        lambda kind, step: events.append((kind, step)), pipeline=compiled.pipeline,
    )
    launch = next(name for name in compiled.pipeline if name.endswith("_StartApp"))
    for phase in ["Starting", "Succeeded"]:
        sink.on_raw_notification(None, "Node.Action." + phase, {"name": launch})
    assert events == [("step_started", 1)]
    ready = launch + "_OrientationReady"
    for phase in ["Starting", "Succeeded"]:
        sink.on_raw_notification(None, "Node.Action." + phase, {"name": ready})
    assert events == [("step_started", 1), ("step_succeeded", 1)]


def test_post_assertion_retry_stays_in_same_step_until_assertion_succeeds(transition_sink):
    events = []
    tasker = type("Tasker", (), {"add_context_sink": lambda *_: 1})()
    sink = install_step_sink(
        tasker, {"action": 2, "assertion": 2, "retry": 2, "wait": 3},
        {2: ["assertion"], 3: ["wait"]}, lambda kind, step: events.append((kind, step)),
    )
    for message, name, candidates in [
        ("NextList.Starting", "previous", ["action"]),
        ("Action.Starting", "action", []),
        ("Action.Succeeded", "action", []),
        ("NextList.Starting", "action", ["assertion"]),
        ("PipelineNode.Failed", "action", []),
        ("NextList.Starting", "action", ["retry"]),
        ("Action.Starting", "retry", []),
        ("Action.Succeeded", "retry", []),
        ("NextList.Starting", "retry", ["action"]),
        ("Action.Starting", "action", []),
        ("Action.Succeeded", "action", []),
        ("NextList.Starting", "action", ["assertion"]),
        ("Action.Starting", "assertion", []),
        ("Action.Succeeded", "assertion", []),
        ("NextList.Starting", "assertion", ["wait"]),
    ]:
        sink.on_raw_notification(None, "Node." + message, {"name": name, "list": candidates})
    assert events == [
        ("step_started", 3), ("step_failed", 3), ("step_started", 3),
        ("step_succeeded", 3), ("step_started", 4),
    ]


def test_skip_moves_directly_to_selected_step_without_starting_skipped_steps(transition_sink):
    events = []
    tasker = type("Tasker", (), {"add_context_sink": lambda *_: 1})()
    sink = install_step_sink(
        tasker, {"skip": 1, "action": 1, "target": 4},
        {1: ["skip", "action"], 4: ["target"]},
        lambda kind, step: events.append((kind, step)),
    )
    sink.on_raw_notification(
        None, "Node.NextList.Starting", {"name": "previous", "list": ["skip", "action"]}
    )
    for phase in ["Starting", "Succeeded"]:
        sink.on_raw_notification(None, "Node.Action." + phase, {"name": "skip"})
    sink.on_raw_notification(
        None, "Node.NextList.Starting", {"name": "skip", "list": ["target"]}
    )
    assert events == [("step_started", 2), ("step_succeeded", 2), ("step_started", 5)]


def test_nested_unmapped_pipeline_failure_does_not_fail_the_waiting_main_step(transition_sink):
    sink, events, _ = transition_sink
    sink.on_raw_notification(
        None, "Node.NextList.Starting", {"name": "click", "list": ["wait"]}
    )
    sink.on_raw_notification(
        None, "Node.NextList.Starting", {"name": "nested-root", "list": ["nested-action"]}
    )
    sink.on_raw_notification(None, "Node.PipelineNode.Failed", {"name": "nested-root"})
    assert events == [("step_started", 4)]
