from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_execution_coordinator import FakeMaaRunner, FakePlatform, _coordinator, _formal_work

from al1s_terminal.execution.maa_runtime import MaaExecutionOutcome
from al1s_terminal.lineup.detector import lineup_boxes
from al1s_terminal.lineup.executor import LineupExecutor
from al1s_terminal.lineup.matching import agreed, name_id


def test_names_do_not_conflate_outfits_or_guess_ambiguous_aliases():
    students = [dict(id=1, aliases=["花子"]), dict(id=2, aliases=["花子（泳装）"])]  # noqa: RUF001
    assert name_id("花子 (泳装)", students) == 2
    assert name_id("花子", students) == 1
    assert name_id("花予", students) is None
    assert name_id("花子", [*students, dict(id=3, aliases=["花子"])]) is None
    assert agreed(1, 1, 0.9, 0.2, 0.9, True)
    assert not agreed(1, 2, 0.9, 0.2, 0.9, True)
    assert not agreed(1, 1, 0.9, 0.2, 0.3, True)
    assert not agreed(1, 1, 0.9, 0.2, 0.9, False)


def test_incomplete_layout_is_not_accepted():
    boxes, valid = lineup_boxes([], 2400, 1080)
    assert boxes == [None] * 12 and not valid


def test_cancel_before_inference_does_not_launch(tmp_path):
    outcome = LineupExecutor(tmp_path).execute({}, {}, 10, lambda: True)
    assert outcome.error_code == "execution_cancelled"


def test_lineup_coordinator_never_calls_phone_or_maa(local_engine, tmp_path):
    _formal_work(local_engine, None)
    coordinator = _coordinator(
        local_engine, tmp_path, FakePlatform(), uuid4(), FakeMaaRunner(), serial=None
    )
    original = coordinator._loader.load
    coordinator._loader.load = lambda work: replace(
        original(work), plan=None, local_manifest={"executor": "lineup-recognition-v1"}
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Linux lineup must not call phone/ADB/Maa execution")

    coordinator._live_serial = forbidden
    coordinator._engine.prepare = forbidden
    coordinator._lineup_executor = SimpleNamespace(
        execute=lambda *args: MaaExecutionOutcome(True, None, {"lineup": {"test": True}})
    )
    assert coordinator.run_once().disposition == "result_queued"
