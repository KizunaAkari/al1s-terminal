from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from al1s_terminal.execution.maa_pipeline import CompiledMaaTask
from al1s_terminal.execution.maa_runtime import MaaRuntimeError
from al1s_terminal.providers.adb import AdbProbeResult
from al1s_terminal.providers.maa import MaaProbeResult


class MaaSmokeRunner(Protocol):
    def run(
        self,
        task: CompiledMaaTask,
        *,
        adb_serial: str,
        timeout_seconds: int,
        capture_failure: bool = False,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class MaaReadinessResult:
    ready: bool
    device_serial: str | None
    error_code: str | None = None
    diagnostic: str | None = None
    cached: bool = False


class MaaRuntimeReadinessProbe:
    """Verify that Maa can connect to and execute against one real ADB device."""

    def __init__(
        self,
        *,
        runner: MaaSmokeRunner,
        data_dir: Path,
        timeout_seconds: int = 30,
    ) -> None:
        self._runner = runner
        self._image_dir = data_dir / "runtime" / "maa-smoke" / "image"
        self._timeout_seconds = timeout_seconds
        self._ready_key: tuple[str, str] | None = None

    def probe(
        self,
        *,
        maa: MaaProbeResult,
        adb: AdbProbeResult,
    ) -> MaaReadinessResult:
        if not maa.available:
            self._ready_key = None
            return MaaReadinessResult(
                ready=False,
                device_serial=None,
                error_code=maa.error_code or "maa_runtime_unavailable",
                diagnostic=maa.diagnostic,
            )
        online = adb.online_devices
        if not online:
            self._ready_key = None
            return MaaReadinessResult(
                ready=False,
                device_serial=None,
                error_code="maa_smoke_device_missing",
                diagnostic="No authorized Android device is online for Maa smoke",
            )
        if len(online) > 1:
            self._ready_key = None
            return MaaReadinessResult(
                ready=False,
                device_serial=None,
                error_code="maa_smoke_device_ambiguous",
                diagnostic="Multiple Android devices are online; Maa smoke requires one device",
            )

        serial = online[0].serial
        key = (maa.version or "unknown", serial)
        if key == self._ready_key:
            return MaaReadinessResult(
                ready=True,
                device_serial=serial,
                cached=True,
            )

        self._image_dir.mkdir(parents=True, exist_ok=True)
        try:
            result = self._runner.run(
                _smoke_task(self._image_dir),
                adb_serial=serial,
                timeout_seconds=self._timeout_seconds,
            )
        except MaaRuntimeError as exc:
            self._ready_key = None
            return MaaReadinessResult(
                ready=False,
                device_serial=serial,
                error_code=exc.code,
                diagnostic=_bounded(str(exc)),
            )
        except Exception as exc:
            self._ready_key = None
            return MaaReadinessResult(
                ready=False,
                device_serial=serial,
                error_code="maa_smoke_failed",
                diagnostic=_bounded(str(exc)),
            )
        if result.get("success") is not True:
            self._ready_key = None
            return MaaReadinessResult(
                ready=False,
                device_serial=serial,
                error_code="maa_smoke_failed",
                diagnostic="Maa smoke completed without a successful result",
            )

        self._ready_key = key
        return MaaReadinessResult(ready=True, device_serial=serial)


def _smoke_task(image_dir: Path) -> CompiledMaaTask:
    entry = "AL1S_Runtime_Smoke"
    return CompiledMaaTask(
        entry=entry,
        pipeline={
            entry: {
                "recognition": "DirectHit",
                "action": "DoNothing",
                "next": [],
            }
        },
        image_dir=image_dir,
        script_hash="al1s-runtime-smoke-v1",
        step_nodes={},
        step_exits={},
        node_steps={},
        active_packages=[],
        source_script={"version": 2, "steps": []},
    )


def _bounded(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ")[:512]
