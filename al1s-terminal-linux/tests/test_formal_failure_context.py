from types import SimpleNamespace

import pytest

from al1s_terminal.execution import maa_adapter, recognition_diagnostics
from al1s_terminal.execution.maa_task_runner import _bounded_diagnostic
from al1s_terminal.execution.step_budget import StepClock


def enrich(diagnostic, pipeline, clock):
    function = getattr(recognition_diagnostics, "enrich_execution_failure", lambda *_: None)
    function(diagnostic, pipeline, clock)


@pytest.mark.parametrize("rule", [None, "Web_test_Global_000_ForStep_003"])
def test_guard_keeps_current_step_and_budget_without_recognition_observations(rule):
    clock = StepClock(lambda: 32.0)
    clock.enter("Web_test:3", rule)
    clock.last = clock.rule_started = 0
    clock.budget = 20
    clock.rule_budget = 30
    pipeline = {"current": {"custom_recognition_param": {
        "key": "Web_test:3", "step_index": 3, "rule": rule,
    }}}
    diagnostic = {"error_type": "MaaStepExecutionStalled"}
    enrich(diagnostic, pipeline, clock)
    assert diagnostic["failed_step"] == {"index": 3, "number": 4}
    context = diagnostic["execution_failure"]
    assert context["step_index"] == 3 and context["elapsed_seconds"] == 32
    assert context["timeout_seconds"] == (30 if rule else 20)
    assert context.get("rule_index") == (0 if rule else None)
    assert "recognition_failure" not in diagnostic
    diagnostic["logs"] = {str(number): "x" * 3000 for number in range(100)}
    assert _bounded_diagnostic(diagnostic)["execution_failure"] == context


def test_guard_does_not_infer_a_step_from_a_foreign_clock_or_condition_rule():
    clock = StepClock(lambda: 32.0)
    clock.enter("foreign:3")
    diagnostic = {"error_type": "MaaStepExecutionStalled"}
    enrich(diagnostic, {"other": {"custom_recognition_param": {
        "key": "Web_test:3", "step_index": 3, "rule": None,
    }}}, clock)
    assert "execution_failure" not in diagnostic
    clock.enter("Web_test:3")
    enrich(diagnostic, {"condition": {"custom_recognition_param": {
        "key": "Web_test:3", "step_index": 3, "rule": None, "condition": True,
    }}}, clock)
    assert "execution_failure" not in diagnostic


def test_formal_run_installs_recognition_sink_without_debug_callback(tmp_path, monkeypatch):
    installed = []

    class StopBeforeExecution(Exception):
        pass

    class Tasker:
        inited = True

        def bind(self, *_):
            return True

        def post_task(self, *_):
            raise StopBeforeExecution

    monkeypatch.setattr(maa_adapter, "register_custom_extensions", lambda *_args, **_kw: None)
    monkeypatch.setattr(maa_adapter, "install_recognition_sink", lambda *_: installed.append(True))
    adapter = object.__new__(maa_adapter.MaaAdapter)
    adapter.available = True
    adapter.yolo = None
    adapter.device = SimpleNamespace(screenshot=lambda: None)
    adapter._maa = {"Resource": lambda: object(), "Tasker": Tasker}
    adapter._screenshot_mode = "raw"
    monkeypatch.setattr(adapter, "_ensure_controller", lambda: object())
    compiled = SimpleNamespace(pipeline={}, node_steps={}, requires_yolo=False, requires_ocr=False,
                               image_dir=tmp_path, entry="entry")
    with pytest.raises(StopBeforeExecution):
        adapter.run({}, {}, compiled_task=compiled)
    assert installed == [True]
