from types import SimpleNamespace

from al1s_terminal.execution.maa_diagnostics import MaaDiagnostics


def test_failed_step_uses_execution_frontier_not_an_old_unselected_branch() -> None:
    compiled = SimpleNamespace(
        node_steps={"first": 0, "old-branch": 0, "second": 1},
        step_exits={0: ["first"], 1: ["second"]},
    )
    detail = SimpleNamespace(
        nodes=[
            SimpleNamespace(name="first", completed=True),
            SimpleNamespace(name="old-branch", completed=False),
            SimpleNamespace(name="second", completed=False),
        ]
    )

    assert MaaDiagnostics._find_failed_step(compiled, detail) == {
        "index": 1,
        "number": 2,
        "node": "second",
    }


def test_image_failure_keeps_recognition_diagnosis() -> None:
    diagnosis = MaaDiagnostics._diagnose_failure(
        {"steps": [{"action": "wait_image"}]},
        {"failed_step": {"index": 0, "node": "image"}, "maa_nodes": [], "error": "timeout"},
    )

    assert diagnosis["stage"] == "recognition"
    assert diagnosis["title"] == "目标图片识别失败"


def test_telemetry_summary_keeps_skip_capture_and_node_recognition() -> None:
    compiled = SimpleNamespace(
        node_steps={"Step_SkipIfImage": 0},
        step_nodes={0: ["Step_SkipIfImage"]},
        script_hash="sample",
    )
    node = SimpleNamespace(
        node_id=7,
        name="Step_SkipIfImage",
        completed=True,
        recognition=SimpleNamespace(
            algorithm="TemplateMatch", hit=True, box=(1, 2, 3, 4),
            best_result=SimpleNamespace(score=0.9),
        ),
        action=None,
    )
    collector = {
        "feedback": [], "custom_actions": [], "numeric_conditions": [],
        "conditional_skip_captures": [{"node": node.name, "capture": {"id": "image"}}],
        "yolo": [], "color_markers": [],
    }
    script = {"steps": [{
        "action": "wait_image",
        "skip_condition": {"enabled": True, "mode": "image", "threshold": 0.8},
    }]}

    summary = MaaDiagnostics()._summarize(
        compiled, script, SimpleNamespace(nodes=[node]), collector
    )

    assert summary["maa_nodes"][0]["recognition"]["score"] == 0.9
    assert summary["steps"][0]["condition"]["hit"] is True
    assert summary["conditional_skips"][0]["capture"] == {"id": "image"}
