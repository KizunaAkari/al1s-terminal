import json
from types import SimpleNamespace

import pytest

from al1s_terminal.execution.maa_definition import MaaDefinitionError, MaaDefinitionLoader
from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler
from al1s_terminal.execution.maa_plan_compiler import MaaPlanCompiler
from al1s_terminal.execution.step_budget import ACTION, register_step_budgets


@pytest.mark.parametrize("action", ["back", "home", "task_view"])
def test_compiler_places_buffers_on_the_real_key_action(tmp_path, action):
    compiled = MaaPipelineCompiler(tmp_path).compile(
        {
            "steps": [
                {
                    "action": action,
                    "wait_before_execution_seconds": 1.25,
                    "wait_after_execution_seconds": 2.5,
                }
            ]
        }
    )
    node = compiled.pipeline[compiled.step_nodes[0][0]]
    assert node["custom_action_param"]["pre_delay"] == 1250
    assert node["custom_action_param"]["post_delay"] == 2500
    source = compiled.pipeline[node["custom_action_param"]["source"]]
    assert source["action"] == "ClickKey"


@pytest.mark.parametrize(
    "success,budget,expected", [(True, 10, 3.0), (False, 10, 1.0), (True, 2, 2.0)]
)
def test_wait_order_failure_and_remaining_budget(monkeypatch, success, budget, expected):
    from al1s_terminal.execution import step_budget

    time = [0.0]
    monkeypatch.setattr(step_budget.time, "monotonic", lambda: time[0])
    monkeypatch.setattr(
        step_budget.time, "sleep", lambda seconds: time.__setitem__(0, time[0] + seconds)
    )
    actions, callbacks = [], {}
    resource = SimpleNamespace(
        register_custom_action=lambda key, value: callbacks.setdefault(key, value),
        register_custom_recognition=lambda *_: True,
    )
    clock = register_step_budgets(resource, object, object, [])
    clock.now = lambda: time[0]
    context = SimpleNamespace(
        tasker=SimpleNamespace(stopping=False),
        run_action=lambda *args: actions.append(time[0]) or SimpleNamespace(success=success),
    )
    config = {
        "key": "main:0",
        "budget": budget,
        "step_index": 0,
        "rule": None,
        "rule_budget": 30,
        "source": "key",
        "pre_delay": 1000,
        "post_delay": 2000,
    }
    argv = SimpleNamespace(
        custom_action_param=json.dumps(config),
        box=(0, 0, 1, 1),
        reco_detail=SimpleNamespace(raw_detail={}),
    )
    assert callbacks[ACTION].run(context, argv) is (success and budget == 10)
    assert actions == [pytest.approx(1)]
    assert time[0] == pytest.approx(expected, abs=0.051)


def test_system_wait_wrapper_compiles_and_an_old_terminal_rejects_it(tmp_path, monkeypatch):
    from test_post_execution_wait import definition_manifest

    from al1s_terminal.execution import maa_definition

    manifest = definition_manifest()
    step = manifest["definitions"]["main"]["steps"][0]
    step.update(
        action_id="back",
        handler_id="maa.pipeline.back",
        parameters={},
        wrappers=[
            {
                "kind": "system_key_wait",
                "handler_id": "maa.wrapper.system_key_wait",
                "parameters": {"before_seconds": 1.5, "after_seconds": 2.25},
            }
        ],
    )
    (task,) = MaaPlanCompiler(tmp_path).compile(MaaDefinitionLoader().load(manifest, resources={}))
    config = task.pipeline[task.step_nodes[0][0]]["custom_action_param"]
    assert config["pre_delay"] == 1500 and config["post_delay"] == 2250
    monkeypatch.setattr(
        maa_definition,
        "REGISTERED_HANDLERS",
        maa_definition.REGISTERED_HANDLERS - {"maa.wrapper.system_key_wait"},
    )
    with pytest.raises(MaaDefinitionError) as failure:
        MaaDefinitionLoader().load(manifest, resources={})
    assert failure.value.code == "maa_wrapper_unregistered"
