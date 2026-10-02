import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from al1s_terminal.execution.maa_definition import MaaDefinitionError, MaaDefinitionLoader
from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler
from al1s_terminal.execution.maa_plan_compiler import MaaPlanCompiler
from al1s_terminal.execution.step_budget import ACTION, register_step_budgets

IMAGE = "data:image/png;base64,YQ=="
STEPS = [
    {"action": "wait_image", "template_base64": IMAGE},
    {"action": "wait_text", "text": "领取"},
    {"action": "click_text", "text": "领取"},
    {
        "action": "wait_click",
        "template_base64": IMAGE,
        "click_mode": "fixed",
        "click": {"x": 1, "y": 2},
        "click_count": 3,
    },
    {
        "action": "wait_click",
        "template_base64": IMAGE,
        "click_mode": "image",
        "click_template_base64": IMAGE,
        "click_count": 3,
    },
    {
        "action": "wait_click",
        "template_base64": IMAGE,
        "click_mode": "match_center",
        "image_branches": [{"template_base64": IMAGE, "click_mode": "match_center"}],
    },
    {
        "action": "wait_click",
        "template_base64": IMAGE,
        "click_mode": "match_offset",
        "template_rect": {"x": 0, "y": 0, "width": 1, "height": 1},
    },
    {"action": "wait_click", "template_base64": IMAGE, "click_mode": "color_marker"},
    {
        "action": "smart_swipe",
        "template_base64": IMAGE,
        "mode": "until_image",
        "swipe": {"x1": 1, "y1": 2, "x2": 3, "y2": 4},
    },
    {
        "action": "smart_swipe",
        "template_base64": IMAGE,
        "mode": "after_image",
        "swipe": {"x1": 1, "y1": 2, "x2": 3, "y2": 4},
    },
    *[
        {
            "action": "recognize_execute",
            "recognition_mode": mode,
            "template_base64": IMAGE,
            "text": "领取",
            "execution_mode": execution,
            "click": {"x": 1, "y": 2},
            "swipe": {"x1": 1, "y1": 2, "x2": 3, "y2": 4, "duration_ms": 300},
            "click_template_base64": IMAGE,
            "execution_count": 3,
            "execution_interval_ms": 120,
        }
        for mode in ["image", "text"]
        for execution in ["fixed_tap", "fixed_swipe", "image_center"]
    ],
]


@pytest.mark.parametrize("step", STEPS)
def test_wait_only_runs_at_success_exits_after_all_actions(tmp_path, step):
    original = MaaPipelineCompiler(tmp_path).compile({"steps": [step, {"action": "home"}]})
    compiled = MaaPipelineCompiler(tmp_path).compile(
        {"steps": [{**step, "wait_after_execution_seconds": 2.75}, {"action": "home"}]}
    )
    exits = compiled.step_exits[0]
    for position, name in enumerate(compiled.step_nodes[0]):
        node = compiled.pipeline[name]
        old_node = original.pipeline[original.step_nodes[0][position]]
        delay = node["custom_action_param"]["post_delay"]
        assert delay == old_node["custom_action_param"]["post_delay"] + (
            2750 if name in exits else 0
        )
        if name in exits:
            assert node["next"] == compiled.step_nodes[1]
        source = compiled.pipeline[node["custom_action_param"]["source"]]
        assert source["post_delay"] == 0
        if source.get("custom_action") == "Al1sRepeatedClick":
            repeated_source = compiled.pipeline[source["custom_action_param"]["source"]]
            assert repeated_source["post_delay"] == 0
            old_source = original.pipeline[old_node["custom_action_param"]["source"]]
            assert (
                source["custom_action_param"]["count"] == old_source["custom_action_param"]["count"]
            )


def test_wait_is_before_assertion_and_skipped_condition_has_no_delay(tmp_path):
    compiled = MaaPipelineCompiler(tmp_path).compile(
        {
            "steps": [
                {
                    "action": "wait_image",
                    "template_base64": IMAGE,
                    "wait_after_execution_seconds": 1.5,
                    "post_assertion": {"enabled": True, "template_base64": IMAGE},
                    "skip_condition": {"enabled": True, "mode": "image", "preview_base64": IMAGE},
                },
                {"action": "home"},
            ]
        }
    )
    nodes = {name: compiled.pipeline[name] for name in compiled.step_nodes[0]}
    main = next(node for name, node in nodes.items() if name.endswith("WaitImage"))
    assert main["custom_action_param"]["post_delay"] == 1500
    assert "Assert" in main["next"][0]
    for name, node in nodes.items():
        if "Assert" in name or "Skip" in name:
            assert node["custom_action_param"]["post_delay"] == 0


@pytest.mark.parametrize("seconds", [None, 0])
def test_zero_or_missing_wait_preserves_existing_pipeline(tmp_path, seconds):
    compiler = MaaPipelineCompiler(tmp_path)
    old = compiler.compile({"steps": [{"action": "wait_text", "text": "领取"}]})
    new = compiler.compile(
        {
            "steps": [
                {"action": "wait_text", "text": "领取", "wait_after_execution_seconds": seconds}
            ]
        }
    )
    assert old.pipeline[old.step_nodes[0][0]]["custom_action_param"]["post_delay"] == 0
    assert new.pipeline[new.step_nodes[0][0]]["custom_action_param"]["post_delay"] == 0


def definition_manifest():
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
                        "action_id": "wait_text",
                        "handler_id": "maa.pipeline.wait_text",
                        "parameters": {"text": "领取"},
                        "wrappers": [
                            {
                                "kind": "wait_after_execution",
                                "handler_id": "maa.wrapper.wait_after_execution",
                                "parameters": {"seconds": 2.75},
                            }
                        ],
                    }
                ],
                "independent_rules": [],
                "cleanup_on_finish": False,
            }
        },
    }


def test_registered_wait_loads_and_compiles_but_old_registry_rejects(tmp_path, monkeypatch):
    from al1s_terminal.execution import maa_definition

    manifest = definition_manifest()
    plan = MaaDefinitionLoader().load(manifest, resources={})
    (compiled,) = MaaPlanCompiler(tmp_path).compile(plan)
    node = compiled.pipeline[compiled.step_nodes[0][0]]
    assert node["custom_action_param"]["post_delay"] == 2750
    monkeypatch.setattr(
        maa_definition,
        "REGISTERED_HANDLERS",
        maa_definition.REGISTERED_HANDLERS - {"maa.wrapper.wait_after_execution"},
    )
    with pytest.raises(MaaDefinitionError) as error:
        MaaDefinitionLoader().load(manifest, resources={})
    assert error.value.code == "maa_wrapper_unregistered"


@pytest.mark.parametrize("seconds", [-1, 14400.1, True, "2", float("inf")])
def test_invalid_wait_cannot_reach_runtime(tmp_path, seconds):
    with pytest.raises(ValueError, match="wait after execution"):
        MaaPlanCompiler(tmp_path).compile(
            MaaDefinitionLoader().load(
                _invalid_manifest(seconds),
                resources={},
            )
        )


def _invalid_manifest(seconds):
    manifest = deepcopy(definition_manifest())
    manifest["definitions"]["main"]["steps"][0]["wrappers"][0]["parameters"]["seconds"] = seconds
    return manifest


@pytest.mark.parametrize("scenario", ["success", "timeout", "cancel", "action_failure"])
def test_wait_callback_is_timed_cancellable_and_does_not_follow_failed_actions(
    tmp_path, monkeypatch, scenario
):
    from al1s_terminal.execution import step_budget

    compiled = MaaPipelineCompiler(tmp_path).compile(
        {
            "steps": [
                {
                    "action": "wait_text",
                    "text": "领取",
                    "wait_after_execution_seconds": 1.25,
                    "timeout_seconds": 0.4 if scenario == "timeout" else 5,
                }
            ]
        }
    )
    node = compiled.pipeline[compiled.step_nodes[0][0]]
    actions = {}
    resource = SimpleNamespace(
        register_custom_action=lambda name, callback: (
            actions.setdefault(name, callback) is callback
        ),
        register_custom_recognition=lambda *_: True,
    )
    failures = []
    clock = register_step_budgets(
        resource, type("Recognition", (), {}), type("Action", (), {}), failures
    )
    now = [0.0]
    clock.now = lambda: now[0]
    clock.last = 0
    context = SimpleNamespace(tasker=SimpleNamespace(stopping=False))

    def sleep(seconds):
        now[0] += seconds
        if scenario == "cancel" and now[0] >= 0.2:
            context.tasker.stopping = True

    monkeypatch.setattr(step_budget.time, "sleep", sleep)
    monkeypatch.setattr(
        step_budget,
        "delegated_action",
        lambda *_: SimpleNamespace(success=scenario != "action_failure"),
    )
    result = actions[ACTION].run(
        context, SimpleNamespace(custom_action_param=json.dumps(node["custom_action_param"]))
    )
    assert result is (scenario == "success")
    assert now[0] == pytest.approx(
        {"success": 1.25, "timeout": 0.45, "cancel": 0.2, "action_failure": 0}[scenario], abs=0.05
    )
    assert not failures
