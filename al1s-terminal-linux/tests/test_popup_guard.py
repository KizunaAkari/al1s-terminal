import json
from types import SimpleNamespace
from unittest.mock import Mock

from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler
from al1s_terminal.execution.popup_guard import register_popup_guard


def test_compiler_scopes_each_rule_counter_to_main_step_and_open_range(tmp_path):
    compiled = MaaPipelineCompiler(tmp_path).compile(
        {
            "steps": [{"action": "wait", "seconds": 1}] * 3,
            "global_popups": [
                {
                    "template_base64": "data:image/png;base64,AQ==",
                    "click_mode": "match_center",
                    "from_step_index": 2,
                }
            ],
        }
    )
    keys = {
        (node.get("attach", {}).get("popup_key"), node.get("attach", {}).get("dsl_step_index"))
        for node in compiled.pipeline.values()
        if node.get("attach", {}).get("popup_key")
    }
    assert len(keys) == 2 and {index for _, index in keys} == {1, 2}


class Recognition:
    AnalyzeResult = staticmethod(lambda **kwargs: kwargs)


def test_only_successful_clicks_count_and_tenth_click_is_rechecked():
    resource, context = Mock(), Mock()
    failures = []
    context.tasker.stopping = False
    register_popup_guard(resource, Recognition, object, failures)
    click = resource.register_custom_action.call_args.args[1]
    recognize = resource.register_custom_recognition.call_args.args[1]
    action = SimpleNamespace(
        box=(0, 0, 1, 1),
        reco_detail=SimpleNamespace(raw_detail={}),
        custom_action_param=json.dumps({"key": "step0-rule0", "source": "source"}),
    )
    reco = SimpleNamespace(
        image=object(),
        custom_recognition_param=json.dumps(
            {
                "key": "step0-rule0",
                "source": "source",
                "step_index": 0,
            }
        ),
    )
    context.run_action.return_value = SimpleNamespace(success=False)
    for _ in range(12):
        assert click.run(context, action) is False
    context.run_action.return_value = SimpleNamespace(success=True)
    for _ in range(10):
        assert click.run(context, action)
    assert not failures
    context.run_action.reset_mock()
    assert click.run(context, action)
    context.run_action.assert_not_called()
    context.run_recognition.return_value = SimpleNamespace(hit=False)
    assert recognize.analyze(context, reco) is None
    assert not failures  # tenth click cleared it: no false failure
    context.run_recognition.return_value = SimpleNamespace(
        hit=True, box=(0, 0, 1, 1), raw_detail={}
    )
    other = SimpleNamespace(
        image=object(),
        custom_recognition_param=json.dumps(
            {
                "key": "step1-rule0",
                "source": "source",
                "step_index": 1,
            }
        ),
    )
    assert recognize.analyze(context, other) is not None
    assert recognize.analyze(context, reco) is None
    assert failures == [
        {"error_type": "MaaIndependentRuleLimit", "step_index": 0, "successful_clicks": 10}
    ]


def test_callback_exceptions_fail_closed():
    resource, context = Mock(), Mock()
    context.tasker.stopping = False
    failures = []
    register_popup_guard(resource, Recognition, object, failures)
    callback = resource.register_custom_recognition.call_args.args[1]
    assert callback.analyze(context, SimpleNamespace(custom_recognition_param="bad")) is None
    assert failures[0]["error_type"] == "MaaIndependentRuleFailed"
