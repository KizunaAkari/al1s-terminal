from copy import deepcopy

import pytest

from al1s_terminal.execution.failure_skip import FailureState
from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler


@pytest.mark.parametrize("target", [None, 3])
def test_failure_skip_has_an_error_only_exit_to_the_existing_target(tmp_path, target):
    condition = {"enabled": True, "mode": "execution_failure"}
    if target is not None:
        condition["skip_to_step_index"] = target
    script = {
        "steps": [
            {"action": "back", "skip_condition": condition},
            {"action": "home"},
            {"action": "task_view"},
        ]
    }
    compiled = MaaPipelineCompiler(tmp_path).compile(script)
    skip_name = next(name for name in compiled.step_nodes[0] if name.endswith("SkipIfFailure"))
    skip = compiled.pipeline[skip_name]
    assert skip["custom_action"] == "Al1sFailureSkip"
    assert skip["next"] == [compiled.step_nodes[(target or 2) - 1][0]]
    normal = compiled.pipeline[compiled.step_nodes[0][0]]
    assert normal["next"] == [compiled.step_nodes[1][0]]
    assert skip_name not in compiled.pipeline[compiled.entry]["next"]
    assert skip_name in normal["on_error"]
    assert skip_name in compiled.pipeline[compiled.entry]["on_error"]


def test_disabled_failure_skip_does_not_change_pipeline(tmp_path):
    step = {"action": "back"}
    compiler = MaaPipelineCompiler(tmp_path)
    before = compiler.compile({"steps": [step]})
    new = deepcopy(step)
    new["skip_condition"] = {"enabled": False, "mode": "execution_failure"}
    after = compiler.compile({"steps": [new]})
    assert not any(name.endswith("SkipIfFailure") for name in after.pipeline)
    assert (
        before.pipeline[before.step_nodes[0][0]]["custom_action"]
        == after.pipeline[after.step_nodes[0][0]]["custom_action"]
    )


def test_timeout_is_owned_by_waiting_step_not_the_previous_parent():
    state = FailureState({"first": {}, "second": {}}, {"first": 0, "second": 1}, None)
    state.observe("Node.NextList.Starting", {"name": "first", "list": ["second"]})
    state.observe("Node.PipelineNode.Failed", {"name": "first", "node_details": {}})
    assert state.failure == {
        "step_index": 1,
        "node": "first",
        "reason": "step_timeout",
        "phase": "recognition",
        "scope": "",
    }


def test_popup_failure_is_not_a_main_step_failure():
    state = FailureState({"popup": {"attach": {"popup_key": "rule"}}}, {"popup": 0}, None)
    state.observe("Node.Action.Failed", {"name": "popup"})
    assert state.failure is None


def test_recognition_miss_is_distinct_from_an_action_failure_after_a_hit():
    pipeline = {
        "target": {"recognition": "TemplateMatch", "attach": {"dsl_step_index": 0}},
        "main": {"custom_recognition_param": {"source": "target"}},
    }
    state = FailureState(pipeline, {"main": 0}, None)
    state.observe("Node.Recognition.Failed", {"name": "target"})
    state.observe("Node.Action.Failed", {"name": "main"})
    assert state.failure["phase"] == "recognition"
    state.observe("Node.Recognition.Succeeded", {"name": "target"})
    state.observe("Node.Action.Failed", {"name": "main"})
    assert state.failure["phase"] == "execution"


def test_old_handler_registry_rejects_new_failure_skip(monkeypatch):
    from al1s_terminal.execution import maa_definition

    raw = {
        "step_index": 1,
        "action_id": "back",
        "handler_id": "maa.pipeline.back",
        "parameters": {},
        "wrappers": [
            {
                "kind": "conditional_skip",
                "handler_id": "maa.wrapper.failure_skip",
                "parameters": {"mode": "execution_failure", "enabled": True},
            }
        ],
    }
    maa_definition.MaaDefinitionLoader()._step(raw, expected_index=1, definitions={}, resources={})
    monkeypatch.setattr(
        maa_definition,
        "REGISTERED_HANDLERS",
        maa_definition.REGISTERED_HANDLERS - {"maa.wrapper.failure_skip"},
    )
    with pytest.raises(maa_definition.MaaDefinitionError, match="unregistered wrapper"):
        maa_definition.MaaDefinitionLoader()._step(
            raw, expected_index=1, definitions={}, resources={}
        )


def test_last_step_cannot_explicitly_jump_outside_the_script(tmp_path):
    with pytest.raises(ValueError, match="later step"):
        MaaPipelineCompiler(tmp_path).compile(
            {
                "steps": [
                    {
                        "action": "back",
                        "skip_condition": {
                            "enabled": True,
                            "mode": "execution_failure",
                            "skip_to_step_index": 2,
                        },
                    }
                ]
            }
        )


def test_recovery_failure_has_its_own_scope_and_index():
    child = "Web_child_Step_000_Back"
    pipeline = {
        "Web_main_Step_005_Recovery": {
            "custom_action_param": {
                "pipeline": {
                    child: {"attach": {"dsl_step_index": 0}, "recognition": "DirectHit"},
                }
            }
        }
    }
    state = FailureState(pipeline, {"Web_main_Step_005_Recovery": 5}, None)
    state.observe("Node.Action.Failed", {"name": child})
    assert state.failure["step_index"] == 0
    assert state.failure["scope"] == "Web_child"


def test_many_failure_skips_keep_error_candidate_lists_bounded(tmp_path):
    compiled = MaaPipelineCompiler(tmp_path).compile(
        {
            "steps": [
                {"action": "back", "skip_condition": {"enabled": True, "mode": "execution_failure"}}
                for _ in range(300)
            ]
        }
    )
    assert max(len(node.get("on_error", [])) for node in compiled.pipeline.values()) <= 3


def test_accepted_failure_ends_old_budget_before_error_exit_capture():
    from al1s_terminal.execution.step_budget import StepClock

    now = [0.0]
    clock = StepClock(lambda: now[0])
    clock.enter("Web_main:1")
    clock.budget = 20
    pipeline = {
        "Web_main_Step_001_Main": {"recognition": "TemplateMatch", "attach": {"dsl_step_index": 1}},
        "skip": {
            "custom_action": "Al1sFailureSkip",
            "custom_recognition": "Al1sFailureSkipRecognition",
            "custom_action_param": {"scope": "Web_main", "step_index": 1, "phase": "recognition"},
        },
    }
    state = FailureState(pipeline, {"Web_main_Step_001_Main": 1}, clock)
    now[0] = 20.1
    state.observe("Node.Recognition.Failed", {"name": "Web_main_Step_001_Main"})
    state.observe("Node.Action.Failed", {"name": "Web_main_Step_001_Main"})
    now[0] = 24
    failures = []
    clock.watchdog(failures)
    assert failures == []
    assert clock.active is None
    assert clock.spent("Web_main:1") == 20.1


def test_target_budget_starts_before_its_first_capture():
    from al1s_terminal.execution.step_budget import StepClock

    now = [0.0]
    clock = StepClock(lambda: now[0])
    pipeline = {
        "target": {
            "custom_recognition": "Al1sStepBudgetRecognition",
            "custom_recognition_param": {"key": "target:5", "budget": 1},
        }
    }
    state = FailureState(pipeline, {"target": 5}, clock)
    state.observe("Node.NextList.Starting", {"name": "skip", "list": ["target"]})
    assert clock.active == "target:5"
    now[0] = 3
    failures = []
    clock.watchdog(failures)
    assert failures == [{"error_type": "MaaStepExecutionStalled"}]


@pytest.mark.parametrize("phase", ["recognition", "execution"])
def test_skip_log_survives_real_sqlite_commit_and_readback(local_engine, phase):
    from datetime import UTC, datetime
    from uuid import uuid4

    from al1s_terminal.app.execution_coordinator import ExecutionCoordinator
    from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
    from al1s_terminal.types import WorkItemKind

    now = datetime(2026, 10, 1, tzinfo=UTC)
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        work = uow.work_items.add(
            kind=WorkItemKind.QUICK_TEST,
            remote_id=uuid4(),
            content_hash="1" * 64,
            target_device_id=None,
            available_at=now,
            payload={},
            now=now,
        )
    coordinator = object.__new__(ExecutionCoordinator)
    coordinator._uow_factory = lambda: LocalUnitOfWork.from_engine(local_engine)
    coordinator._clock = lambda: now
    coordinator._record_quick_event(work, f"failure_skipped:{phase}:step_timeout", 7)
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        (event,) = uow.quick_test_events.pending_batch()
    assert event.kind == "log"
    assert event.step_number is None
    assert event.code == f"maa_failure_skipped:{phase}:step_timeout:7"
