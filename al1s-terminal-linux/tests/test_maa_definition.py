from __future__ import annotations

from pathlib import Path

import pytest

from al1s_terminal.execution.maa_definition import MaaDefinitionError, MaaDefinitionLoader


def _compiled(handler_id: str = "maa.pipeline.wait") -> dict[str, object]:
    return {
        "schema_version": 1,
        "compiler_version": "maa-registered-actions-v1",
        "script_version_id": "version-1",
        "script_name": "课程表脚本",
        "script_type": "module_process",
        "target": {"application_package": "com.example.game"},
        "steps": [
            {
                "step_index": 1,
                "action_id": "wait",
                "handler_id": handler_id,
                "parameters": {"seconds": 1},
                "wrappers": [],
            }
        ],
        "independent_rules": [],
        "cleanup_on_finish": False,
    }


def test_screenshot_handler_loads_and_compiles_to_existing_registered_action(
    tmp_path: Path,
) -> None:
    from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler
    from al1s_terminal.execution.maa_plan_compiler import MaaPlanCompiler

    definition = _compiled("maa.registered.screenshot")
    definition["steps"][0].update(action_id="screenshot", parameters={"timeout_seconds": 10})
    manifest = {
        "schema_version": 1,
        "definition_type": "script",
        "entry_definition_key": "main",
        "definitions": {"main": definition},
    }
    plan = MaaDefinitionLoader().load(manifest, resources={})
    assert plan.modules[0].steps[0]["action_id"] == "screenshot"
    (compiled,) = MaaPlanCompiler(tmp_path).compile(plan)
    assert any(
        node.get("custom_action") == MaaPipelineCompiler.SCREENSHOT_ACTION
        for node in compiled.pipeline.values()
    )


@pytest.mark.parametrize("first_position", [0, 1])
def test_loader_preserves_strategy_order_and_waits(tmp_path: Path, first_position: int) -> None:
    manifest = {
        "schema_version": 1,
        "definition_type": "strategy",
        "modules": [
            {"position": first_position + 1, "definition_key": "second", "wait_after_ms": 200},
            {"position": first_position, "definition_key": "first", "wait_after_ms": 100},
        ],
        "definitions": {"first": _compiled(), "second": _compiled()},
    }

    plan = MaaDefinitionLoader().load(manifest, resources={})

    assert [item.definition_key for item in plan.modules] == ["first", "second"]
    assert [item.script_name for item in plan.modules] == ["课程表脚本", "课程表脚本"]
    assert [item.wait_after_ms for item in plan.modules] == [100, 200]
    assert set(plan.definitions) == {"first", "second"}


@pytest.mark.parametrize("positions", [[-1, 1], [True, 2], [0.5, 1], ["0", 1], [0, 0]])
def test_loader_rejects_invalid_strategy_positions(positions: list[object]) -> None:
    manifest = {
        "schema_version": 1,
        "definition_type": "strategy",
        "modules": [{"position": p, "definition_key": "main"} for p in positions],
        "definitions": {"main": _compiled()},
    }
    with pytest.raises(MaaDefinitionError) as captured:
        MaaDefinitionLoader().load(manifest, resources={})
    assert captured.value.code == "maa_strategy_module_invalid"


def test_loader_rejects_unknown_handler_before_execution() -> None:
    manifest = {
        "schema_version": 1,
        "definition_type": "script",
        "entry_definition_key": "main",
        "definitions": {"main": _compiled("maa.pipeline.shell")},
    }

    with pytest.raises(MaaDefinitionError) as captured:
        MaaDefinitionLoader().load(manifest, resources={})
    assert captured.value.code == "maa_handler_unregistered"


def test_loader_requires_resource_to_be_in_controlled_mapping(tmp_path: Path) -> None:
    definition = _compiled()
    steps = definition["steps"]
    assert isinstance(steps, list)
    step = steps[0]
    assert isinstance(step, dict)
    step["parameters"] = {"template_base64": {"$resource": "template:1"}}
    manifest = {
        "schema_version": 1,
        "definition_type": "script",
        "entry_definition_key": "main",
        "definitions": {"main": definition},
    }

    with pytest.raises(MaaDefinitionError) as captured:
        MaaDefinitionLoader().load(manifest, resources={})
    assert captured.value.code == "maa_resource_missing"

    resource = tmp_path / "template.png"
    resource.write_bytes(b"png")
    plan = MaaDefinitionLoader().load(manifest, resources={"template:1": resource})
    assert plan.resources["template:1"] == resource


def test_ocr_assertion_handler_loads_and_old_registry_rejects_it(tmp_path, monkeypatch):
    import al1s_terminal.execution.maa_definition as module
    from al1s_terminal.execution.maa_plan_compiler import MaaPlanCompiler

    definition = _compiled()
    definition["steps"][0]["wrappers"] = [
        {
            "kind": "post_assertion",
            "handler_id": "maa.wrapper.post_assertion_text",
            "parameters": {
                "enabled": True,
                "recognition_mode": "text",
                "text": "Ready[1]",
                "max_retries": 1,
                "timeout_seconds": 5,
                "poll_interval_seconds": 1,
            },
        }
    ]
    manifest = {
        "schema_version": 1,
        "definition_type": "script",
        "entry_definition_key": "main",
        "definitions": {"main": definition},
    }
    plan = MaaDefinitionLoader().load(manifest, resources={})
    (compiled,) = MaaPlanCompiler(tmp_path).compile(plan)
    assert compiled.requires_ocr
    assert any(node.get("expected") == [r"Ready\[1\]"] for node in compiled.pipeline.values())
    monkeypatch.setattr(
        module,
        "REGISTERED_HANDLERS",
        module.REGISTERED_HANDLERS - {"maa.wrapper.post_assertion_text"},
    )
    with pytest.raises(MaaDefinitionError):
        MaaDefinitionLoader().load(manifest, resources={})
