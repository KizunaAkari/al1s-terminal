import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from al1s_terminal.execution.pipeline_steps import PipelineStepCompiler
from al1s_terminal.execution.repeated_click import register_repeated_click, wrap_repeated_clicks


def test_compiler_preserves_large_count_and_interval():
    assert PipelineStepCompiler._repeat_fields({"click_count": 25, "click_interval_ms": 25000}) == {
        "repeat": 25,
        "repeat_delay": 25000,
    }


def test_count_has_no_fixed_product_cap():
    assert PipelineStepCompiler._repeat_fields({"click_count": 1_000_001})["repeat"] == 1_000_001


@pytest.mark.parametrize("value", [0, -1, True, 1.5])
def test_invalid_count_is_rejected_not_silently_clamped(value):
    with pytest.raises(ValueError):
        PipelineStepCompiler._repeat_fields({"click_count": value})


@pytest.mark.parametrize("action", ["Click", "Swipe"])
def test_wrapper_preserves_target_and_budget_without_duplicating_delays(action):
    pipeline = {
        "click": {
            "action": action,
            "repeat": 25,
            "repeat_delay": 25000,
            "target": [10, 20],
            "post_delay": 300,
            "attach": {"dsl_step_index": 0},
        }
    }
    wrap_repeated_clicks(pipeline, [{"timeout_seconds": 100}], (64, 96))
    config = pipeline["click"]["custom_action_param"]
    assert config["count"] == "25" and config["budget_seconds"] == 100
    assert pipeline["click"]["post_delay"] == 300
    assert pipeline[config["source"]]["post_delay"] == 0
    assert pipeline[config["source"]]["target"] == [10, 20]
    assert pipeline[config["source"]]["action"] == action


def test_first_action_failure_stops_remaining_clicks():
    resource = Mock()
    failures = []
    register_repeated_click(resource, object, failures)
    callback = resource.register_custom_action.call_args.args[1]
    context = Mock()
    context.tasker.stopping = False
    context.run_action.return_value = SimpleNamespace(success=False)
    args = SimpleNamespace(
        box=(1, 2, 3, 4),
        reco_detail=SimpleNamespace(raw_detail={}),
        custom_action_param=json.dumps(
            {
                "source": "source",
                "count": 25,
                "interval_ms": 0,
                "budget_seconds": 30,
                "size": None,
                "step_index": 0,
            }
        ),
    )
    assert not callback.run(context, args)
    context.run_action.assert_called_once()
    context.tasker.post_stop.assert_not_called()
