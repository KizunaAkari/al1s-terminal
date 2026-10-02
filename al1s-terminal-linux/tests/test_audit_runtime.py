from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from al1s_terminal.execution.maa_runtime import (
    MaaExecutionEngine,
    MaaExecutionOutcome,
    MaaRuntimeError,
    PreparedMaaExecution,
)
from al1s_terminal.execution.result_policy import final_diagnostic


@pytest.mark.parametrize("reference", [{"artifact_id": "pending"}, {"path": "capture.png"}])
def test_pending_capture_is_not_marked_absent_and_input_is_unchanged(reference):
    source = {"failure_screenshot": reference}
    result = final_diagnostic(MaaExecutionOutcome(False, "maa_pipeline_failed", source))
    assert "failure_screenshot_error" not in result
    assert source == {"failure_screenshot": reference}


def test_end_failure_is_not_executed_twice():
    runner = Mock()
    runner.run.side_effect = [{"success": True}] * 3 + [
        MaaRuntimeError("maa_pipeline_failed", "end")
    ]
    with pytest.raises(MaaRuntimeError):
        MaaExecutionEngine(compiler=Mock(), runner=runner).execute(
            prepared(), adb_serial="test", timeout_seconds=100
        )
    assert [call.args[0] for call in runner.run.call_args_list] == [0, 1, 2, 3]


def prepared(kind="strategy"):
    modules = tuple(
        SimpleNamespace(definition_key=str(i), script_name=str(i), wait_after_ms=0, script_type=t)
        for i, t in enumerate(("module_start", "module_process", "module_process", "module_end"))
    )
    return PreparedMaaExecution(
        SimpleNamespace(definition_type=kind, modules=modules), (0, 1, 2, 3)
    )


@pytest.mark.parametrize("raised", [False, True])
def test_failed_process_runs_end_only_and_preserves_primary_failure(raised):
    runner = Mock()
    failure = MaaRuntimeError(
        "maa_pipeline_failed", "failed", {"failure_screenshot": {"path": "original.png"}}
    )
    runner.run.side_effect = [
        {"success": True},
        failure if raised else {"success": False, "error_code": "maa_pipeline_failed"},
        {"success": True},
    ]
    engine = MaaExecutionEngine(compiler=Mock(), runner=runner)
    if raised:
        with pytest.raises(MaaRuntimeError) as caught:
            engine.execute(prepared(), adb_serial="test", timeout_seconds=100)
        assert caught.value.diagnostic["failure_screenshot"]["path"] == "original.png"
        diagnostic = caught.value.diagnostic
    else:
        outcome = engine.execute(prepared(), adb_serial="test", timeout_seconds=100)
        assert not outcome.passed and outcome.error_code == "maa_pipeline_failed"
        diagnostic = outcome.diagnostic
    assert [c.args[0] for c in runner.run.call_args_list] == [0, 1, 3]
    assert diagnostic["end_script_cleanup"]["status"] == "completed"
    assert 0 < runner.run.call_args.kwargs["timeout_seconds"] <= 60


def test_end_failure_not_repeated_and_does_not_replace_process_error():
    runner = Mock()
    runner.run.side_effect = [
        MaaRuntimeError("post_assertion_failed", "first"),
        MaaRuntimeError("maa_runtime_failed", "cleanup"),
    ]
    with pytest.raises(MaaRuntimeError) as caught:
        MaaExecutionEngine(compiler=Mock(), runner=runner).execute(
            prepared(), adb_serial="test", timeout_seconds=60
        )
    assert caught.value.code == "post_assertion_failed"
    assert caught.value.diagnostic["end_script_cleanup"]["status"] == "failed"
    assert [c.args[0] for c in runner.run.call_args_list] == [0, 3]


def test_single_step_never_runs_end():
    runner = Mock()
    runner.run.side_effect = MaaRuntimeError("maa_pipeline_failed", "failed")
    with pytest.raises(MaaRuntimeError):
        MaaExecutionEngine(compiler=Mock(), runner=runner).execute(
            prepared("quick_test"), adb_serial="test", timeout_seconds=60
        )
    assert runner.run.call_count == 1


def test_cancel_still_has_bounded_end_and_remains_cancelled():
    runner = Mock()
    runner.run.return_value = {"success": True}
    with pytest.raises(MaaRuntimeError) as caught:
        MaaExecutionEngine(compiler=Mock(), runner=runner).execute(
            prepared(), adb_serial="test", timeout_seconds=60, cancel_requested=lambda: True
        )
    assert caught.value.code == "execution_cancelled"
    assert [c.args[0] for c in runner.run.call_args_list] == [3]
    assert runner.run.call_args.kwargs["cancel_requested"] is None
