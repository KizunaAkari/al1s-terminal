from types import SimpleNamespace

import pytest

from al1s_terminal.execution.action_dispatch import RegisteredActions
from al1s_terminal.execution.maa_definition import MaaDefinitionError, MaaDefinitionLoader
from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler
from al1s_terminal.execution.maa_plan_compiler import MaaPlanCompiler


def test_reuses_one_template_and_rechecks_before_subsequent_clicks(tmp_path):
    compiled = MaaPipelineCompiler(tmp_path).compile(
        {
            "steps": [
                {
                    "action": "recognize_execute",
                    "recognition_mode": "image",
                    "execution_mode": "match_center",
                    "template_base64": "data:image/png;base64,YQ==",
                    "execution_count": 3,
                    "threshold": 0.9,
                    "poll_interval_seconds": 0.35,
                    "search_region": {"x": 1, "y": 2, "width": 20, "height": 30},
                }
            ]
        }
    )
    assert len(compiled.step_nodes[0]) == 1
    node = compiled.pipeline[compiled.step_nodes[0][0]]
    repeat = compiled.pipeline[node["custom_action_param"]["source"]]
    assert repeat["custom_action_param"]["recheck_match"] is True
    assert repeat["custom_action_param"]["poll_interval_ms"] == 350
    click = compiled.pipeline[repeat["custom_action_param"]["source"]]
    assert click["action"] == "Click" and click["attach"]["click_match_center"] is True
    assert click["target"] is True
    assert click["threshold"] == 0.9 and click["roi"] == [1, 2, 20, 30]
    assert len(list(compiled.image_dir.iterdir())) == 1


@pytest.mark.parametrize(
    "box,expected", [((10, 20, 31, 21), (25, 30, 1, 1)), ((4, 6, 20, 10), (13, 10, 1, 1))]
)
def test_registered_dispatch_uses_the_matched_center_pixel_instead_of_the_entire_box(box, expected):
    calls = []
    context = SimpleNamespace(
        get_node_data=lambda _: {
            "action": "Click",
            "target": True,
            "attach": {"click_match_center": True},
        },
        run_action=lambda *args: calls.append(args),
    )
    argv = SimpleNamespace(box=box, reco_detail=SimpleNamespace(raw_detail={"score": 0.99}))
    RegisteredActions(SimpleNamespace()).invoke(context, argv, "source")
    assert calls[0][1] == expected


def test_registered_mode_loads_and_an_old_registry_rejects_before_execution(tmp_path, monkeypatch):
    from al1s_terminal.execution import maa_definition

    manifest = {
        "schema_version": 1,
        "definition_type": "script",
        "entry_definition_key": "main",
        "definitions": {
            "main": {
                "schema_version": 1,
                "compiler_version": "maa-registered-actions-v2",
                "script_version_id": "v",
                "script_name": "test",
                "script_type": "module_process",
                "target": {},
                "steps": [
                    {
                        "step_index": 1,
                        "action_id": "recognize_execute",
                        "handler_id": "maa.pipeline.recognize_execute",
                        "parameters": {
                            "recognition_mode": "image",
                            "execution_mode": "match_center",
                            "template_base64": "data:image/png;base64,YQ==",
                        },
                        "wrappers": [
                            {
                                "kind": "recognize_match_center",
                                "handler_id": "maa.wrapper.recognize_match_center",
                                "parameters": {},
                            }
                        ],
                    }
                ],
                "independent_rules": [],
                "cleanup_on_finish": False,
            }
        },
    }
    (task,) = MaaPlanCompiler(tmp_path).compile(MaaDefinitionLoader().load(manifest, resources={}))
    assert len(task.step_nodes[0]) == 1
    monkeypatch.setattr(
        maa_definition,
        "REGISTERED_HANDLERS",
        maa_definition.REGISTERED_HANDLERS - {"maa.wrapper.recognize_match_center"},
    )
    with pytest.raises(MaaDefinitionError) as error:
        MaaDefinitionLoader().load(manifest, resources={})
    assert error.value.code == "maa_wrapper_unregistered"
