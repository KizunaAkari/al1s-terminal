import json
from types import SimpleNamespace

import pytest

from al1s_terminal.execution.action_dispatch import RegisteredActions
from al1s_terminal.execution.repeated_click import register_repeated_click, wrap_repeated_clicks


def repetition(monkeypatch, *, count=2, budget=20, interval=5000, center=True):
    from al1s_terminal.execution import repeated_click

    now = [0.0]
    monkeypatch.setattr(repeated_click.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        repeated_click.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    pipeline = {
        "main": {
            "recognition": "TemplateMatch",
            "action": "Click",
            "target": True,
            "repeat": count,
            "repeat_delay": interval,
            "rate_limit": 100,
            "attach": {"dsl_step_index": 0, "click_match_center": center},
        }
    }
    wrap_repeated_clicks(
        pipeline, [{"timeout_seconds": budget, "poll_interval_seconds": 0.1}], (96, 64)
    )
    config = pipeline["main"]["custom_action_param"]
    resource = RegisteredActions(SimpleNamespace(register_custom_action=lambda *_: True))
    failures, actions, recognitions = [], [], []
    register_repeated_click(resource, object, failures)
    controller = SimpleNamespace(
        cached_image=SimpleNamespace(shape=(64, 96, 3)),
        post_screencap=lambda: SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True)),
    )
    context = SimpleNamespace(
        tasker=SimpleNamespace(stopping=False, controller=controller),
        get_node_data=lambda name: pipeline[name],
    )
    responses = []

    def recognize(source, image):
        recognitions.append((now[0], source, image))
        return responses.pop(0) if responses else SimpleNamespace(hit=False)

    def action(source, box, detail):
        actions.append((now[0], box, json.loads(detail)))
        return SimpleNamespace(success=True)

    context.run_recognition, context.run_action = recognize, action
    argv = SimpleNamespace(
        task_detail=None,
        node_name="main",
        box=(10, 20, 13, 9),
        reco_detail=SimpleNamespace(raw_detail={"score": 0.99}),
        custom_action_param=json.dumps(config),
    )
    return (
        resource.actions["Al1sRepeatedClick"],
        context,
        argv,
        now,
        responses,
        actions,
        recognitions,
        failures,
    )


def hit(box):
    return SimpleNamespace(hit=True, box=box, raw_detail={"score": 0.98})


def test_rechecks_moving_target_after_the_configured_five_second_interval(monkeypatch):
    callback, context, argv, _now, responses, actions, recognitions, failures = repetition(
        monkeypatch
    )
    responses.append(hit((40, 10, 13, 9)))
    assert callback.run(context, argv)
    assert [action[1] for action in actions] == [(16, 24, 1, 1), (46, 14, 1, 1)]
    assert actions[1][0] - actions[0][0] == pytest.approx(5)
    assert len(recognitions) == 1 and not failures


def test_temporary_misses_do_not_click_the_old_position_or_consume_execution_count(monkeypatch):
    callback, context, argv, _now, responses, actions, recognitions, failures = repetition(
        monkeypatch, count=3
    )
    responses.extend([None, SimpleNamespace(hit=False), hit((40, 10, 13, 9)), hit((60, 30, 13, 9))])
    assert callback.run(context, argv)
    assert [action[1] for action in actions] == [(16, 24, 1, 1), (46, 14, 1, 1), (66, 34, 1, 1)]
    assert len(recognitions) == 4 and actions[1][0] == pytest.approx(5.2)
    assert actions[2][0] - actions[1][0] == pytest.approx(5)
    assert not failures


def test_missing_target_uses_the_remaining_budget_without_an_extra_click(monkeypatch):
    callback, context, argv, now, _responses, actions, recognitions, failures = repetition(
        monkeypatch, budget=6
    )
    assert not callback.run(context, argv)
    assert len(actions) == 1 and len(recognitions) >= 1
    assert now[0] == pytest.approx(6)
    assert failures[0]["error_type"] == "MaaClickRepeatTimeout"


@pytest.mark.parametrize("cause", ["cancel", "size", "click_failure", "budget"])
def test_recheck_failure_does_not_allow_an_old_or_late_click(monkeypatch, cause):
    callback, context, argv, now, _responses, actions, _recognitions, _failures = repetition(
        monkeypatch
    )

    def changed(source, image):
        if cause == "cancel":
            context.tasker.stopping = True
        if cause == "budget":
            now[0] = 21
        return hit((40, 10, 13, 9))

    context.run_recognition = changed
    if cause == "size":

        def capture():
            if actions:
                context.tasker.controller.cached_image.shape = (96, 64, 3)
            return SimpleNamespace(wait=lambda: SimpleNamespace(succeeded=True))

        context.tasker.controller.post_screencap = capture
    if cause == "click_failure":

        def click(*args):
            actions.append(args)
            return SimpleNamespace(success=False)

        context.run_action = click
    assert not callback.run(context, argv)
    assert len(actions) == 1


def test_fixed_target_repetition_keeps_its_existing_no_recheck_behavior(monkeypatch):
    callback, context, argv, _now, _responses, actions, recognitions, failures = repetition(
        monkeypatch, center=False
    )
    assert callback.run(context, argv)
    assert len(actions) == 2 and not recognitions and not failures
    assert actions[0][1] == actions[1][1]
