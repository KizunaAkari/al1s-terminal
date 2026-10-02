import json
from pathlib import Path

import pytest

from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler

EXAMPLES = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "backend" / "al1s-backend" / "contracts" / "maa-dsl-v2.examples.json"
    ).read_text(
        encoding="utf-8"
    )
)


def test_shared_v2_document_compiles(tmp_path):
    assert set(EXAMPLES["core_actions"]) <= MaaPipelineCompiler.supported_actions
    task = MaaPipelineCompiler(tmp_path).compile(EXAMPLES["valid"])
    assert len(task.step_nodes) == len(EXAMPLES["valid"]["steps"])
    assert task.requires_ocr
    complex_task = MaaPipelineCompiler(tmp_path).compile(EXAMPLES["valid_complex"])
    assert len(complex_task.step_nodes) == len(EXAMPLES["valid_complex"]["steps"])
    wait = complex_task.pipeline[complex_task.step_nodes[1][0]]
    assert wait["custom_recognition_param"]["consecutive_match_count"] == 3


def test_shared_malformed_action_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="does not support action"):
        MaaPipelineCompiler(tmp_path).compile(EXAMPLES["invalid"])


def test_shared_wrong_coordinate_type_does_not_compile(tmp_path):
    with pytest.raises(ValueError):
        MaaPipelineCompiler(tmp_path).compile(EXAMPLES["invalid_typed"])


def test_shared_wrong_branch_threshold_does_not_compile(tmp_path):
    with pytest.raises(ValueError):
        MaaPipelineCompiler(tmp_path).compile(EXAMPLES["invalid_branch"])
