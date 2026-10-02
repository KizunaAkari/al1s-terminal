from unittest.mock import Mock

from al1s_terminal.execution.artifact_store import _select_captures
from al1s_terminal.execution.capture_budget import CaptureBudget
from al1s_terminal.execution.maa_task_runner import _bounded_diagnostic


def test_budget_stops_creating_files_and_new_script_has_own_budget():
    capture = Mock(return_value={"path": "image.png"})
    budget = CaptureBudget(capture)
    for _ in range(256):
        assert budget() == {"path": "image.png"}
    assert budget() == {"discarded": "screenshot_limit"}
    assert capture.call_count == 256
    CaptureBudget(capture)()
    assert capture.call_count == 257


def test_failure_priority_per_script_and_mirrored_evidence_dedup():
    captures = [
        ({"path": f"{group}-{index}.png", "mime": "image/png"},
         ("modules", str(group), "captures", str(index)))
        for group in range(2) for index in range(256)
    ]
    failure = {"path": "failure.png", "mime": "image/png"}
    captures.extend([(failure, ("modules", "0", "result", "failure_screenshot")),
                     (dict(failure), ("modules", "0", "mirror"))])
    video = {"path": "recording.mp4", "mime": "video/mp4"}
    captures.append((video, ("recordings", "0")))
    selected, discarded = _select_captures(captures)
    assert len(selected) == 513
    assert len(discarded) == 1
    assert selected[0][0] is failure
    assert discarded[0][0]["path"] == "0-255.png"
    assert any(item[0] is video for item in selected)


def test_diagnostic_truncation_keeps_all_capture_file_references():
    source = {
        "custom_actions": [{"result": {"path": f"image-{i}.png", "mime": "image/png"}}
                           for i in range(256)],
        "failure_screenshot": {"path": "failure.png", "mime": "image/png"},
        "logs": {str(i): "x" * 2048 for i in range(100)},
    }
    result = _bounded_diagnostic(source)
    assert result["truncated"]
    assert len(result["capture_files"]) == 257
    assert result["failure_screenshot"]["path"] == "failure.png"
