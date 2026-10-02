import json
from types import SimpleNamespace

import pytest

from al1s_terminal.execution.maa_definition import MaaDefinitionError, MaaDefinitionLoader
from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler
from al1s_terminal.execution.maa_plan_compiler import MaaPlanCompiler
from al1s_terminal.execution.step_budget import ACTION, RECOGNITION, register_step_budgets

IMAGE = "data:image/png;base64,YQ=="


def runtime(tmp_path, **settings):
    step = {
        "action": "wait_image",
        "template_base64": IMAGE,
        "consecutive_match_count": 3,
        "timeout_seconds": 90,
        "poll_interval_seconds": 1,
        **settings,
    }
    compiled = MaaPipelineCompiler(tmp_path).compile(
        {
            "steps": [step, dict(step)],
            "global_popups": [{"template_base64": IMAGE, "click_mode": "match_center"}],
        }
    )
    callbacks, actions, failures = {}, {}, []
    resource = SimpleNamespace(
        register_custom_recognition=lambda name, cb: callbacks.setdefault(name, cb) is cb,
        register_custom_action=lambda name, cb: actions.setdefault(name, cb) is cb,
    )
    recognition_type = type("Recognition", (), {"AnalyzeResult": SimpleNamespace})
    clock = register_step_budgets(resource, recognition_type, type("Action", (), {}), failures)
    now = [0.0]
    clock.now = lambda: now[0]
    clock.last = clock.last_activity = 0
    context = SimpleNamespace(tasker=SimpleNamespace(stopping=False))
    nodes = [compiled.pipeline[compiled.step_nodes[index][0]] for index in range(2)]

    def sample(config=None, hit=True):
        context.run_recognition = lambda *_: (
            SimpleNamespace(hit=hit, box=(1, 2, 3, 4), raw_detail={"score": 0.9})
            if hit is not None
            else None
        )
        return callbacks[RECOGNITION].analyze(
            context,
            SimpleNamespace(
                custom_recognition_param=json.dumps(config or nodes[0]["custom_recognition_param"]),
                image=object(),
            ),
        )

    return SimpleNamespace(
        compiled=compiled,
        nodes=nodes,
        sample=sample,
        actions=actions,
        context=context,
        clock=clock,
        now=now,
        failures=failures,
    )


@pytest.mark.parametrize("miss", [False, None])
def test_transient_visibility_and_miss_require_a_new_complete_streak(tmp_path, miss):
    run = runtime(tmp_path)
    assert run.sample() is None
    assert run.sample() is None
    assert run.sample(hit=miss) is None
    assert run.sample() is None
    assert run.sample() is None
    result = run.sample()
    assert result.box == (1, 2, 3, 4) and result.detail == {"score": 0.9}
    assert not run.failures


def test_popup_candidate_misses_preserve_streak_but_a_popup_hit_resets_it(tmp_path):
    run = runtime(tmp_path)
    popup = next(
        node["custom_recognition_param"]
        for node in run.compiled.pipeline.values()
        if node.get("custom_recognition") == RECOGNITION
        and node["custom_recognition_param"]["rule"]
        and node["custom_recognition_param"]["condition"]
    )
    assert run.sample(popup, hit=False) is None
    assert run.sample() is None
    assert run.sample(popup, hit=False) is None
    assert run.sample() is None
    assert run.sample(popup) is not None
    assert run.sample() is None
    assert run.sample(popup, hit=False) is None
    assert run.sample() is None
    assert run.sample(popup, hit=False) is None
    assert run.sample() is not None
    assert not run.failures


def test_other_steps_recovery_and_a_new_execution_do_not_reuse_counts(tmp_path):
    run = runtime(tmp_path)
    assert run.sample() is None
    assert run.sample() is None
    second = run.nodes[1]["custom_recognition_param"]
    assert run.sample(second) is None
    assert run.sample() is None  # Returning from another step starts at one.
    assert run.sample() is None
    reset = {
        **run.nodes[0]["custom_recognition_param"],
        "reset": True,
        "consecutive_match_count": 1,
    }
    assert run.sample(reset) is not None
    assert run.sample() is None
    assert run.sample() is None
    fresh = runtime(tmp_path)
    assert fresh.sample() is None
    assert run.sample() is not None


@pytest.mark.parametrize("count", [None, 1])
def test_old_and_single_round_configuration_pass_on_first_hit(tmp_path, count):
    run = runtime(tmp_path, consecutive_match_count=count)
    assert run.sample() is not None


@pytest.mark.parametrize("count", [0, -1, 1.5, True, "3", {}, float("inf")])
def test_invalid_runtime_parameters_cannot_silently_pass(tmp_path, count):
    with pytest.raises(ValueError, match="consecutive match count"):
        runtime(tmp_path, consecutive_match_count=count)


@pytest.mark.parametrize("stop", ["timeout", "cancel"])
def test_confirmation_obeys_step_budget_and_cancellation(tmp_path, monkeypatch, stop):
    from al1s_terminal.execution import step_budget

    run = runtime(tmp_path, timeout_seconds=2)
    invoked = []
    monkeypatch.setattr(step_budget, "delegated_action", lambda *_: invoked.append(True))
    assert run.sample() is None
    run.now[0] = 1
    assert run.sample() is None
    if stop == "timeout":
        run.now[0] = 2
        assert run.sample().detail == {"step_timeout": True}
    else:
        run.context.tasker.stopping = True
        assert run.sample() is None
    result = run.actions[ACTION].run(
        run.context,
        SimpleNamespace(
            custom_action_param=json.dumps(run.nodes[0]["custom_action_param"]),
        ),
    )
    assert result is False and invoked == []
    assert not run.failures


def test_confirmation_precedes_post_wait_and_assertion_and_preserves_poll_interval(
    tmp_path, monkeypatch
):
    from al1s_terminal.execution import step_budget

    run = runtime(
        tmp_path,
        wait_after_execution_seconds=1.25,
        post_assertion={
            "enabled": True,
            "template_base64": IMAGE,
        },
    )
    node = run.nodes[0]
    assert run.compiled.pipeline[run.compiled.entry]["rate_limit"] == 1000
    assert node["custom_action_param"]["post_delay"] == 1250
    assert any("Assert" in str(name) for name in node["next"])
    assert run.sample() is None
    assert run.sample() is None
    assert run.now[0] == 0
    assert run.sample() is not None
    monkeypatch.setattr(
        step_budget.time, "sleep", lambda seconds: run.now.__setitem__(0, run.now[0] + seconds)
    )
    monkeypatch.setattr(step_budget, "delegated_action", lambda *_: SimpleNamespace(success=True))
    assert (
        run.actions[ACTION].run(
            run.context,
            SimpleNamespace(
                custom_action_param=json.dumps(node["custom_action_param"]),
            ),
        )
        is True
    )
    assert run.now[0] == pytest.approx(1.25)


def definition_manifest(count=3):
    return {
        "schema_version": 1,
        "definition_type": "script",
        "entry_definition_key": "main",
        "definitions": {
            "main": {
                "schema_version": 1,
                "compiler_version": "maa-registered-actions-v2",
                "script_version_id": "version-1",
                "script_name": "test",
                "script_type": "module_process",
                "target": {},
                "steps": [
                    {
                        "step_index": 1,
                        "action_id": "wait_image",
                        "handler_id": "maa.pipeline.wait_image",
                        "parameters": {"template_base64": IMAGE},
                        "wrappers": [
                            {
                                "kind": "wait_image_stability",
                                "handler_id": "maa.wrapper.wait_image_stability",
                                "parameters": {"consecutive_match_count": count},
                            }
                        ],
                    }
                ],
                "independent_rules": [],
                "cleanup_on_finish": False,
            }
        },
    }


def test_registered_definition_restores_rounds_and_older_registry_rejects(tmp_path, monkeypatch):
    from al1s_terminal.execution import maa_definition

    manifest = definition_manifest()
    plan = MaaDefinitionLoader().load(manifest, resources={})
    (compiled,) = MaaPlanCompiler(tmp_path).compile(plan)
    node = compiled.pipeline[compiled.step_nodes[0][0]]
    assert node["custom_recognition_param"]["consecutive_match_count"] == 3
    monkeypatch.setattr(
        maa_definition,
        "REGISTERED_HANDLERS",
        maa_definition.REGISTERED_HANDLERS - {"maa.wrapper.wait_image_stability"},
    )
    with pytest.raises(MaaDefinitionError) as error:
        MaaDefinitionLoader().load(manifest, resources={})
    assert error.value.code == "maa_wrapper_unregistered"


def test_stability_wrapper_rejects_wrong_action_and_invalid_count(tmp_path):
    manifest = definition_manifest()
    manifest["definitions"]["main"]["steps"][0].update(
        action_id="wait_text", handler_id="maa.pipeline.wait_text"
    )
    with pytest.raises(MaaDefinitionError, match="wait_image"):
        MaaPlanCompiler(tmp_path).compile(MaaDefinitionLoader().load(manifest, resources={}))
    with pytest.raises(ValueError, match="consecutive match count"):
        MaaPlanCompiler(tmp_path).compile(
            MaaDefinitionLoader().load(definition_manifest(0), resources={})
        )
