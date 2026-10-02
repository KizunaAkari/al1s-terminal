from __future__ import annotations

from pathlib import Path

from al1s_terminal.execution.maa_definition import MaaDefinitionLoader
from al1s_terminal.execution.maa_plan_compiler import MaaPlanCompiler


def test_registered_wait_and_image_resource_compile_to_native_pipeline(tmp_path: Path) -> None:
    image = tmp_path / "source.png"
    image.write_bytes(b"template-image")
    definition = {
        "schema_version": 1,
        "compiler_version": "maa-registered-actions-v1",
        "script_version_id": "version-1",
        "script_name": "课程表脚本",
        "script_type": "module_process",
        "target": {},
        "steps": [
            {
                "step_index": 1,
                "action_id": "wait_image",
                "handler_id": "maa.pipeline.wait_image",
                "parameters": {
                    "template_base64": {
                        "$resource": "template:1",
                        "media_type": "image/png",
                    },
                    "threshold": 0.85,
                    "timeout_seconds": 10,
                },
                "wrappers": [],
            }
        ],
        "independent_rules": [],
        "cleanup_on_finish": False,
    }
    manifest = {
        "schema_version": 1,
        "definition_type": "script",
        "entry_definition_key": "main",
        "definitions": {"main": definition},
    }
    plan = MaaDefinitionLoader().load(manifest, resources={"template:1": image})

    compiled = MaaPlanCompiler(tmp_path).compile(plan)

    assert len(compiled) == 1
    assert compiled[0].entry in compiled[0].pipeline
    assert any(node.get("recognition") == "TemplateMatch" for node in compiled[0].pipeline.values())
    assert any(compiled[0].image_dir.iterdir())
