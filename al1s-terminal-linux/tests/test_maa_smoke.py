from __future__ import annotations

from pathlib import Path
from typing import Any

from al1s_terminal.execution.maa_pipeline import CompiledMaaTask
from al1s_terminal.execution.maa_runtime import MaaRuntimeError
from al1s_terminal.execution.maa_smoke import MaaRuntimeReadinessProbe
from al1s_terminal.providers.adb import AdbDevice, AdbProbeResult
from al1s_terminal.providers.maa import MaaProbeResult
from al1s_terminal.types import AdbDeviceState


class _Runner:
    def __init__(self, error: MaaRuntimeError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[CompiledMaaTask, str]] = []

    def run(
        self,
        task: CompiledMaaTask,
        *,
        adb_serial: str,
        timeout_seconds: int,
        capture_failure: bool = False,
    ) -> dict[str, Any]:
        assert timeout_seconds == 30
        assert capture_failure is False
        self.calls.append((task, adb_serial))
        if self.error is not None:
            raise self.error
        return {"success": True}


def _maa() -> MaaProbeResult:
    return MaaProbeResult(
        available=True,
        version="5.12.1",
        ocr_available=False,
        ocr_model_dir="/models/ocr",
    )


def _adb(*serials: str) -> AdbProbeResult:
    return AdbProbeResult(
        binary_available=True,
        binary_path="adb",
        devices=tuple(
            AdbDevice(
                serial=serial,
                state=AdbDeviceState.ONLINE,
                model="phone",
                product="phone",
                transport_id=str(index),
            )
            for index, serial in enumerate(serials, start=1)
        ),
    )


def test_smoke_runs_non_mutating_pipeline_and_caches_success(tmp_path: Path) -> None:
    runner = _Runner()
    probe = MaaRuntimeReadinessProbe(runner=runner, data_dir=tmp_path)

    first = probe.probe(maa=_maa(), adb=_adb("phone-1"))
    second = probe.probe(maa=_maa(), adb=_adb("phone-1"))

    assert first.ready is True
    assert first.cached is False
    assert second.ready is True
    assert second.cached is True
    assert len(runner.calls) == 1
    task, serial = runner.calls[0]
    assert serial == "phone-1"
    assert task.pipeline[task.entry]["recognition"] == "DirectHit"
    assert task.pipeline[task.entry]["action"] == "DoNothing"


def test_smoke_does_not_cache_transient_failure(tmp_path: Path) -> None:
    runner = _Runner(MaaRuntimeError("maa_adb_connection_failed", "cannot connect"))
    probe = MaaRuntimeReadinessProbe(runner=runner, data_dir=tmp_path)

    first = probe.probe(maa=_maa(), adb=_adb("phone-1"))
    second = probe.probe(maa=_maa(), adb=_adb("phone-1"))

    assert first.ready is False
    assert first.error_code == "maa_adb_connection_failed"
    assert second.ready is False
    assert len(runner.calls) == 2


def test_smoke_requires_exactly_one_online_device(tmp_path: Path) -> None:
    runner = _Runner()
    probe = MaaRuntimeReadinessProbe(runner=runner, data_dir=tmp_path)

    missing = probe.probe(maa=_maa(), adb=_adb())
    ambiguous = probe.probe(maa=_maa(), adb=_adb("phone-1", "phone-2"))

    assert missing.error_code == "maa_smoke_device_missing"
    assert ambiguous.error_code == "maa_smoke_device_ambiguous"
    assert runner.calls == []
