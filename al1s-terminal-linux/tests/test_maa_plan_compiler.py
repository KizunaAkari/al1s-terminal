from __future__ import annotations

from pathlib import Path

from al1s_terminal.execution.maa_definition import MaaDefinitionLoader, MaaExecutionPlan
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


def _resource_plan(
    image: Path,
    references: list[tuple[str, str]],
) -> MaaExecutionPlan:
    steps = [
        {
            "step_index": index,
            "action_id": "wait_image",
            "handler_id": "maa.pipeline.wait_image",
            "parameters": {
                "template_base64": {"$resource": key, "media_type": media_type},
                "threshold": 0.85,
                "timeout_seconds": 10,
            },
            "wrappers": [],
        }
        for index, (key, media_type) in enumerate(references, start=1)
    ]
    manifest = {
        "schema_version": 1,
        "definition_type": "script",
        "entry_definition_key": "main",
        "definitions": {
            "main": {
                "schema_version": 1,
                "compiler_version": "maa-registered-actions-v1",
                "script_version_id": "version-1",
                "script_name": "重复资源脚本",
                "script_type": "module_process",
                "target": {},
                "steps": steps,
                "independent_rules": [],
                "cleanup_on_finish": False,
            }
        },
    }
    return MaaDefinitionLoader().load(
        manifest,
        resources={"template:png": image, "template:jpg": image},
    )


def test_compile_reuses_each_path_and_media_type_once_with_real_assets(
    tmp_path: Path, monkeypatch
) -> None:
    image = tmp_path / "source.png"
    image.write_bytes(b"template-image")
    plan = _resource_plan(
        image,
        [
            ("template:png", "image/png"),
            ("template:png", "image/png"),
            ("template:jpg", "image/jpeg"),
        ],
    )
    original_read_bytes = Path.read_bytes
    reads: list[Path] = []

    def counted_read_bytes(path: Path) -> bytes:
        if path == image:
            reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
    task = MaaPlanCompiler(tmp_path).compile(plan)[0]

    assert reads == [image, image]
    templates = [
        node["template"]
        for node in task.pipeline.values()
        if node.get("recognition") == "TemplateMatch"
    ]
    png_templates = [template for template in templates if template.endswith(".png")]
    jpg_templates = [template for template in templates if template.endswith(".jpg")]
    assert png_templates and jpg_templates
    assert len(set(png_templates)) == 1
    assert len(set(jpg_templates)) == 1
    assert (task.image_dir / png_templates[0]).read_bytes() == b"template-image"
    assert (task.image_dir / jpg_templates[0]).read_bytes() == b"template-image"


def test_compile_cache_is_local_to_one_compile_and_reads_updated_file(
    tmp_path: Path,
) -> None:
    image = tmp_path / "source.png"
    image.write_bytes(b"first")
    plan = _resource_plan(image, [("template:png", "image/png")])
    compiler = MaaPlanCompiler(tmp_path)

    first = compiler.compile(plan)[0]
    first_template = next(
        node["template"]
        for node in first.pipeline.values()
        if node.get("recognition") == "TemplateMatch"
    )
    assert (first.image_dir / first_template).read_bytes() == b"first"

    image.write_bytes(b"second")
    second = compiler.compile(plan)[0]
    second_template = next(
        node["template"]
        for node in second.pipeline.values()
        if node.get("recognition") == "TemplateMatch"
    )
    assert (second.image_dir / second_template).read_bytes() == b"second"
