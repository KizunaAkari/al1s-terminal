from pathlib import Path

from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler


def test_compiled_identity_preserves_unicode_numbers_and_key_order(tmp_path: Path) -> None:
    script = {
        "version": 2,
        "steps": [{"action": "wait", "seconds": 0.5}],
        "extra": {
            "unicode": "中文😀", "boolean": True, "value": None,
            "numbers": [1, 1.25, 1e-6, -0.0],
        },
    }
    compiler = MaaPipelineCompiler(tmp_path)
    first = compiler.compile(script)
    reordered = compiler.compile(dict(reversed(list(script.items()))))
    assert first.script_hash == "d4a6b4d329f761c9"
    assert reordered.script_hash == first.script_hash
    assert reordered.pipeline == first.pipeline
    assert reordered.image_dir == first.image_dir
