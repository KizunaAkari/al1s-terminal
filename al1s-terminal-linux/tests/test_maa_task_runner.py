from __future__ import annotations

import time
from multiprocessing.connection import Connection
from pathlib import Path

import pytest

from al1s_terminal.execution.maa_pipeline import CompiledMaaTask
from al1s_terminal.execution.maa_runtime import MaaRuntimeError
from al1s_terminal.execution.maa_task_runner import (
    MaaFrameworkTaskRunner,
    MaaWorkerEvent,
    MaaWorkerReply,
    MaaWorkerRequest,
    WorkerTarget,
    _bounded_adb_script,
)


def _compiled_task(tmp_path: Path) -> CompiledMaaTask:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    return CompiledMaaTask(
        entry="Entry",
        pipeline={"Entry": {"action": "DoNothing"}},
        image_dir=image_dir,
        script_hash="a" * 16,
        step_nodes={},
        step_exits={},
        node_steps={},
        active_packages=[],
        source_script={"version": 2, "steps": []},
    )


def _successful_worker(send_end: Connection, request: MaaWorkerRequest) -> None:
    send_end.send(
        MaaWorkerReply(
            True,
            {
                "success": True,
                "serial": request.adb_serial,
                "script_hash": request.task.script_hash,
            },
        )
    )
    send_end.close()


def _event_worker(send_end: Connection, request: MaaWorkerRequest) -> None:
    if request.emit_events:
        send_end.send(MaaWorkerEvent("step_started", 1))
        send_end.send(MaaWorkerEvent("step_succeeded", 1))
    send_end.send(MaaWorkerReply(True, {"success": True}))
    send_end.close()


def test_runner_forwards_native_step_events(tmp_path: Path) -> None:
    events: list[tuple[str, int]] = []
    result = _runner(tmp_path, _event_worker).run(
        _compiled_task(tmp_path),
        adb_serial="phone-1",
        timeout_seconds=5,
        on_event=lambda kind, step: events.append((kind, step)),
    )
    assert result["success"] is True
    assert events == [("step_started", 1), ("step_succeeded", 1)]


def _failed_worker(send_end: Connection, _request: MaaWorkerRequest) -> None:
    send_end.send(
        MaaWorkerReply(
            False,
            {"success": False, "error": "OCR model missing"},
            "maa_ocr_model_unavailable",
        )
    )
    send_end.close()


def _large_result_worker(send_end: Connection, _request: MaaWorkerRequest) -> None:
    send_end.send(MaaWorkerReply(True, {"evidence": "x" * 500_000}))
    send_end.close()


def test_runner_drains_result_before_waiting_for_child_exit(tmp_path: Path) -> None:
    result = _runner(tmp_path, _large_result_worker).run(
        _compiled_task(tmp_path),
        adb_serial="phone-1",
        timeout_seconds=5,
    )
    assert len(result["evidence"]) == 500_000


def _hanging_worker(send_end: Connection, _request: MaaWorkerRequest) -> None:
    try:
        time.sleep(60)
    finally:
        send_end.close()


def _runner(tmp_path: Path, worker: WorkerTarget) -> MaaFrameworkTaskRunner:
    return MaaFrameworkTaskRunner(
        data_dir=tmp_path,
        adb_path="adb",
        ocr_model_dir=None,
        worker_target=worker,
    )


def test_runner_returns_child_result(tmp_path: Path) -> None:
    result = _runner(tmp_path, _successful_worker).run(
        _compiled_task(tmp_path),
        adb_serial="phone-1",
        timeout_seconds=5,
    )

    assert result["success"] is True
    assert result["serial"] == "phone-1"


def test_runner_preserves_stable_provider_error(tmp_path: Path) -> None:
    with pytest.raises(MaaRuntimeError) as caught:
        _runner(tmp_path, _failed_worker).run(
            _compiled_task(tmp_path),
            adb_serial="phone-1",
            timeout_seconds=5,
        )

    assert caught.value.code == "maa_ocr_model_unavailable"
    assert caught.value.diagnostic["error"] == "OCR model missing"


def test_runner_terminates_hung_native_worker(tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(MaaRuntimeError) as caught:
        _runner(tmp_path, _hanging_worker).run(
            _compiled_task(tmp_path),
            adb_serial="phone-1",
            timeout_seconds=1,
        )

    assert caught.value.code == "execution_timeout"
    assert time.monotonic() - started < 5


def test_runner_terminates_native_worker_when_cancellation_arrives(tmp_path: Path) -> None:
    started = time.monotonic()
    with pytest.raises(MaaRuntimeError) as caught:
        _runner(tmp_path, _hanging_worker).run(
            _compiled_task(tmp_path),
            adb_serial="phone-1",
            timeout_seconds=5,
            cancel_requested=lambda: time.monotonic() - started >= 0.2,
        )

    assert caught.value.code == "execution_cancelled"
    assert time.monotonic() - started < 3


def test_bounded_adb_script_preserves_arguments_and_quotes_paths() -> None:
    script = _bounded_adb_script(
        "/usr/bin/core utils/timeout",
        "/opt/android tools/adb",
        30,
    )

    assert (
        "exec '/usr/bin/core utils/timeout' --foreground -k 5s 30s '/opt/android tools/adb' \"$@\""
    ) in script
    assert 'cat|*"com.shxyke.MaaTouch.App"' in script
    assert 'if [ "$1" = "kill-server" ]; then exit 0; fi' in script


def test_termination_kills_residual_children_even_when_worker_exits_on_term(monkeypatch):
    from unittest.mock import Mock

    from al1s_terminal.execution import maa_task_runner as module

    process = Mock()
    process.is_alive.return_value = False
    signals = []
    monkeypatch.setattr(module, "_isolated_process_group", lambda p: 456)
    monkeypatch.setattr(
        module, "_kill_process_group", lambda group, sig: signals.append((group, sig))
    )
    monkeypatch.setattr(module, "_platform_signal", lambda: type("Signals", (), {"SIGKILL": 9}))
    MaaFrameworkTaskRunner._terminate(process)
    assert signals == [(456, 15), (456, 9)]
    process.kill.assert_not_called()
