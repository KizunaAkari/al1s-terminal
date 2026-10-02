from __future__ import annotations

from al1s_terminal.execution.maa_task_runner import _bounded_diagnostic
from al1s_terminal.execution.recognition_diagnostics import (
    RecognitionDiagnostics,
    enrich_recognition_failure,
)
from al1s_terminal.execution.step_budget import StepClock

RULE = "Web_test_Global_000_ForStep_003"
NODE = RULE + "_Click_ScreenSource"


def pipeline():
    return {
        NODE: {
            "recognition": "TemplateMatch",
            "threshold": 0.85,
            "attach": {"dsl_step_index": 3, "popup_key": RULE, "popup_condition": False},
        }
    }


def event(tracker, message="Failed", score=0.84406, name=NODE):
    tracker.observe(
        "Node.Recognition." + message,
        {
            "name": name,
            "reco_details": {
                "algorithm": "TemplateMatch",
                "detail": {
                    "best": None,
                    "all": [{"score": score, "private_text": "secret"}],
                },
            },
        },
    )


def clock():
    value = StepClock(lambda: 32.0)
    value.enter("Web_test:3", RULE)
    value.rule_started = 0
    value.rule_budget = 30
    return value


def test_first_notification_hit_resets_then_second_has_nine_real_misses():
    tracker = RecognitionDiagnostics(pipeline())
    event(tracker, score=0.6)
    event(tracker, "Succeeded", score=0.999998)
    for score in [
        0.844058,
        0.844060,
        0.844060,
        0.844058,
        0.844058,
        0.844058,
        0.844058,
        0.844058,
        0.844058,
    ]:
        event(tracker, score=score)
        tracker.observe("Node.RecognitionNode.Failed", {"name": NODE})
    diagnostic = {"error_type": "MaaStepExecutionStalled"}
    enrich_recognition_failure(diagnostic, tracker, clock())
    assert diagnostic["failed_step"] == {"index": 3, "number": 4}
    assert diagnostic["recognition_failure"] == {
        "step_index": 3,
        "rule_index": 0,
        "stage": "click_target",
        "algorithm": "TemplateMatch",
        "consecutive_misses": 9,
        "best_score": 0.84406,
        "threshold": 0.85,
        "timeout_seconds": 30,
        "elapsed_seconds": 32,
    }
    assert "secret" not in str(diagnostic)


def test_normal_popup_polling_and_success_do_not_report_a_main_failure():
    tracker = RecognitionDiagnostics(pipeline())
    event(tracker)
    assert tracker.snapshot() is None
    event(tracker, "Succeeded")
    assert tracker.snapshot(clock()) is None


def test_missing_or_nonfinite_score_is_omitted():
    tracker = RecognitionDiagnostics(pipeline())
    for score in (None, float("nan"), float("inf"), True, "0.8"):
        event(tracker, score=score)
    detail = tracker.snapshot(clock())
    assert detail["consecutive_misses"] == 5
    assert "best_score" not in detail


def test_main_and_assertion_contexts_and_failed_step_scope():
    for stage, algorithm in (("step", "TemplateMatch"), ("post-assertion", "OCR")):
        tracker = RecognitionDiagnostics(
            {
                "main": {
                    "recognition": algorithm,
                    "attach": {"dsl_step_index": 1, "maa_project_role": stage},
                }
            }
        )
        event(tracker, name="main")
        assert tracker.snapshot(failed_index=2) is None
        assert tracker.snapshot(failed_index=1)["stage"] == (
            "post_assertion" if stage == "post-assertion" else "recognition"
        )


def test_capture_stall_has_context_without_invented_score_or_misses():
    tracker = RecognitionDiagnostics(pipeline())
    event(tracker, "Starting")
    assert tracker.snapshot(clock())["consecutive_misses"] == 0
    assert "best_score" not in tracker.snapshot(clock())


def test_storage_is_bounded_and_other_errors_and_steps_are_not_overwritten():
    nodes = {
        str(i): {"recognition": "TemplateMatch", "attach": {"dsl_step_index": i}}
        for i in range(200)
    }
    tracker = RecognitionDiagnostics(nodes)
    for name in nodes:
        event(tracker, name=name)
    assert len(tracker.observations) == 128
    diagnostic = {"error_type": "MaaScreenSizeMismatch"}
    enrich_recognition_failure(diagnostic, tracker)
    assert "recognition_failure" not in diagnostic
    tracker = RecognitionDiagnostics(pipeline())
    event(tracker)
    diagnostic = {"error_type": "MaaPipelineFailed", "failed_step": {"index": 1, "number": 2}}
    enrich_recognition_failure(diagnostic, tracker, clock())
    assert "recognition_failure" not in diagnostic


def test_large_receipt_keeps_the_compact_summary():
    value = {"recognition_failure": {"step_index": 3, "consecutive_misses": 9}, "raw": "x" * 100000}
    assert _bounded_diagnostic(value)["recognition_failure"] == value["recognition_failure"]


def test_enrichment_keeps_existing_native_node_and_action_metadata():
    tracker = RecognitionDiagnostics({"main": {
        "recognition": "TemplateMatch", "attach": {"dsl_step_index": 0},
    }})
    event(tracker, name="main")
    diagnostic = {"error_type": "MaaPipelineFailed", "failed_step": {
        "index": 0, "number": 1, "node": "main", "action": "wait_image",
    }}
    enrich_recognition_failure(diagnostic, tracker)
    assert diagnostic["failed_step"] == {
        "index": 0, "number": 1, "node": "main", "action": "wait_image",
    }
