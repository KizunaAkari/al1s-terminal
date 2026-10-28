import json
from types import SimpleNamespace

from al1s_terminal.execution.custom_extensions import register_custom_extensions
from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler
from tests.test_custom_extensions import _Resource


def test_retry_compiler_names_only_capped_target_nodes(tmp_path):
    script = {
        "steps": [
            {
                "action": "wait_click",
                "click_mode": "color_marker",
                "template_base64": "unused",
                "match_max_clicks": 3,
                "failure_retry": {
                    "enabled": True,
                    "max_retries": 1,
                    "process_script_name": "refresh",
                    "process_script": {
                        "script_type": "module_process",
                        "steps": [{"action": "back"}],
                    },
                },
            }
        ]
    }
    compiled = MaaPipelineCompiler(tmp_path).compile(script)
    retry = next(
        node
        for node in compiled.pipeline.values()
        if node.get("custom_action") == MaaPipelineCompiler.FAILURE_RETRY_PROCESS_ACTION
    )
    names = retry["custom_action_param"].get("reset_hit_count_nodes", [])
    assert len(names) == 1 and "ColorMarkerClick" in names[0]
    assert compiled.pipeline[names[0]]["max_hit"] == 3
    assert all("FailureRetry" not in name for name in names)


def test_successful_recovery_resets_target_counts_but_failed_recovery_does_not():
    resource = _Resource()
    collector = {
        key: []
        for key in [
            "custom_actions",
            "numeric_conditions",
            "yolo",
            "color_markers",
            "feedback",
            "conditional_skip_captures",
        ]
    }
    register_custom_extensions(
        resource,
        collector,
        device=SimpleNamespace(screenshot=lambda: None),
        yolo=SimpleNamespace(),
        maa={
            name: type(name, (), {})
            for name in [
                "CustomAction",
                "CustomRecognition",
                "JOCR",
                "JRecognitionType",
                "JTemplateMatch",
            ]
        },
        parse_json=json.loads,
        first_number=lambda _: None,
        rect_tuple=lambda _: (0, 0, 1, 1),
        find_color_markers=lambda *_: [],
    )
    action = resource.actions[MaaPipelineCompiler.FAILURE_RETRY_PROCESS_ACTION]
    for success in [True, False]:
        cleared = []
        context = SimpleNamespace(
            run_task=lambda *_, success=success: SimpleNamespace(
                status=SimpleNamespace(succeeded=success), task_id=1, nodes=[]
            ),
            clear_hit_count=lambda name, cleared=cleared: cleared.append(name) or True,
        )
        parameters = {
            "entry": "refresh",
            "pipeline": {"refresh": {}},
            "reset_hit_count_nodes": ["touch"],
        }
        assert (
            action.run(
                context,
                SimpleNamespace(custom_action_param=json.dumps(parameters), node_name="retry"),
            )
            is success
        )
        assert cleared == (["touch"] if success else [])
    context = SimpleNamespace(
        run_task=lambda *_: SimpleNamespace(
            status=SimpleNamespace(succeeded=True), task_id=1, nodes=[]
        ),
        clear_hit_count=lambda _: False,
    )
    assert (
        action.run(
            context, SimpleNamespace(custom_action_param=json.dumps(parameters), node_name="retry")
        )
        is False
    )
