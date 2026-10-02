import pytest

from al1s_terminal.execution.step_budget import StepClock, wrap_step_budgets


@pytest.mark.parametrize(
    "step, expected",
    [
        ({"action": "smart_swipe"}, 45),
        ({"action": "wait", "seconds": 60}, 61),
        ({"action": "launch", "wait_seconds": 90}, 91),
        ({"action": "smart_swipe", "swipe_for_seconds": 200}, 201),
        ({"timeout_seconds": 14400}, 14400),
        ({"timeout_seconds": 10, "seconds": 60}, 10),
    ],
)
def test_default_budget_includes_declared_action_time_and_explicit_limit_wins(step, expected):
    pipeline = {
        "step": {"recognition": "DirectHit", "action": "DoNothing", "attach": {"dsl_step_index": 0}}
    }
    wrap_step_budgets(pipeline, [step], "script")
    assert pipeline["step"]["custom_action_param"]["budget"] == expected


def test_stalled_capture_is_stopped_but_normal_expiry_can_recover():
    now = [0.0]
    clock, failures = StepClock(lambda: now[0]), []
    clock.enter("step0")
    clock.budget = 1
    now[0] = 1.1
    clock.watchdog(failures)
    assert not failures
    now[0] = 3
    clock.watchdog(failures)
    assert failures == [{"error_type": "MaaStepExecutionStalled"}]


def test_popup_pause_preserves_elapsed_and_each_step_is_independent():
    now = [0.0]
    clock = StepClock(lambda: now[0])
    clock.enter("step0")
    now[0] = 2
    clock.enter("step0", "popup0")
    now[0] = 90
    assert clock.spent("step0") == 2
    clock.enter("step0")
    now[0] = 93
    assert clock.spent("step0") == 5
    clock.enter("step1")
    now[0] = 94
    assert clock.spent("step0") == 5 and clock.spent("step1") == 1
    clock.enter("step0")
    assert clock.spent("step0") == 5
    clock.reset("step0")
    assert clock.spent("step0") == 0


def test_wrapper_preserves_recovery_and_moves_delays_inside_budget():
    pipeline = {
        "root": {"next": ["step"], "timeout": 20000},
        "step": {
            "recognition": "DirectHit",
            "action": "Click",
            "target": [1, 2],
            "pre_delay": 50,
            "post_delay": 100,
            "on_error": ["recovery"],
            "attach": {"dsl_step_index": 0},
        },
    }
    wrap_step_budgets(pipeline, [{"timeout_seconds": 4}], "script")
    node = pipeline["step"]
    assert node["on_error"] == ["recovery"]
    assert node["pre_delay"] == node["post_delay"] == 0
    config = node["custom_action_param"]
    assert config["pre_delay"] == 50 and config["post_delay"] == 100
    assert pipeline[config["source"]]["target"] == [1, 2]
