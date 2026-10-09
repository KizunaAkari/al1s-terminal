from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from al1s_terminal.types import AdbDeviceState


class AdbProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AdbDevice:
    serial: str
    state: AdbDeviceState
    model: str | None
    product: str | None
    transport_id: str | None


@dataclass(frozen=True, slots=True)
class AdbProbeResult:
    binary_available: bool
    binary_path: str
    devices: tuple[AdbDevice, ...]
    error_code: str | None = None
    diagnostic: str | None = None

    @property
    def online_devices(self) -> tuple[AdbDevice, ...]:
        return tuple(item for item in self.devices if item.state is AdbDeviceState.ONLINE)


CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


class AdbProvider:
    def __init__(
        self,
        *,
        adb_path: str | Path | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        configured = str(adb_path).strip() if adb_path is not None else ""
        self._adb_path = configured or shutil.which("adb") or "adb"
        self._runner = runner or _run_command

    @property
    def binary_path(self) -> str:
        return self._adb_path

    def probe(self) -> AdbProbeResult:
        try:
            result = self._runner((self._adb_path, "devices", "-l"), 10.0)
        except FileNotFoundError:
            return AdbProbeResult(
                binary_available=False,
                binary_path=self._adb_path,
                devices=(),
                error_code="adb_not_installed",
                diagnostic="ADB executable was not found",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return AdbProbeResult(
                binary_available=False,
                binary_path=self._adb_path,
                devices=(),
                error_code="adb_probe_failed",
                diagnostic=_bounded(str(exc)),
            )
        if result.returncode != 0:
            return AdbProbeResult(
                binary_available=True,
                binary_path=self._adb_path,
                devices=(),
                error_code="adb_server_unavailable",
                diagnostic=_bounded(result.stderr.strip() or "adb devices failed"),
            )
        return AdbProbeResult(
            binary_available=True,
            binary_path=self._adb_path,
            devices=parse_adb_devices(result.stdout),
        )

    def require_device(self, serial: str | None = None) -> AdbDevice:
        probe = self.probe()
        if not probe.binary_available or probe.error_code is not None:
            raise AdbProviderError(
                probe.error_code or "adb_unavailable",
                probe.diagnostic or "ADB is unavailable",
            )
        if serial:
            selected = next((item for item in probe.devices if item.serial == serial), None)
            if selected is None:
                raise AdbProviderError("adb_device_missing", "Bound Android device is not present")
            if selected.state is not AdbDeviceState.ONLINE:
                raise AdbProviderError(
                    _state_error_code(selected.state),
                    f"Bound Android device is {selected.state.value}",
                )
            return selected
        online = probe.online_devices
        if not online:
            raise AdbProviderError("adb_device_missing", "No authorized Android device is online")
        if len(online) > 1:
            raise AdbProviderError(
                "adb_device_ambiguous",
                "Multiple Android devices are online and no bound serial was selected",
            )
        return online[0]

    def reconnect_offline(self, serial: str) -> bool:
        # Recheck just before reconnect: never kick a device that came online.
        current = next((d for d in self.probe().devices if d.serial == serial), None)
        if current is None or current.state is not AdbDeviceState.OFFLINE:
            return False
        try:
            return self._runner((self._adb_path, "-s", serial, "reconnect"), 5).returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def smoke_screenshot(self, serial: str) -> bytes:
        device = self.require_device(serial)
        try:
            result = self._runner(
                (self._adb_path, "-s", device.serial, "exec-out", "screencap", "-p"),
                30.0,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AdbProviderError("adb_screenshot_failed", _bounded(str(exc))) from exc
        # The injected test runner uses text CompletedProcess values. Production returns
        # latin-1 losslessly only through the dedicated binary executor added with Maa runtime.
        payload = result.stdout.encode("latin-1")
        if result.returncode != 0 or not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AdbProviderError(
                "adb_screenshot_failed",
                _bounded(result.stderr.strip() or "ADB screenshot did not return a PNG"),
            )
        return payload

    def prepare_screen(self, serial: str, *, allowed: Callable[[], bool] = lambda: True) -> None:
        from al1s_terminal.providers.screen_preparation import prepare_screen

        if not serial.strip():
            raise AdbProviderError("adb_serial_missing", "Bound device required")
        prepare_screen(self._adb_path, serial, self._runner, allowed=allowed)

    def force_stop_and_home(self, serial: str, package_name: str | None) -> None:
        commands: list[tuple[str, ...]] = []
        if package_name:
            package = package_name.split("/", 1)[0].strip()
            if package:
                commands.append(("shell", "am", "force-stop", package))
        commands.append(("shell", "input", "keyevent", "KEYCODE_HOME"))
        for arguments in commands:
            try:
                result = self._runner((self._adb_path, "-s", serial, *arguments), 30.0)
            except (OSError, subprocess.SubprocessError) as exc:
                raise AdbProviderError("device_cleanup_failed", _bounded(str(exc))) from exc
            if result.returncode != 0:
                raise AdbProviderError(
                    "device_cleanup_failed",
                    _bounded(result.stderr.strip() or "ADB device cleanup failed"),
                )

    def foreground_package(self, serial: str) -> str:
        """Read the current resumed Android activity; never trust a browser-supplied package."""
        device = self.require_device(serial)
        for command in (
            ("shell", "dumpsys", "activity", "activities"),
            ("shell", "dumpsys", "window", "windows"),
        ):
            try:
                result = self._runner((self._adb_path, "-s", device.serial, *command), 10.0)
            except (OSError, subprocess.SubprocessError) as exc:
                raise AdbProviderError("foreground_probe_failed", _bounded(str(exc))) from exc
            if result.returncode != 0:
                continue
            for line in result.stdout.splitlines():
                if not any(
                    marker in line
                    for marker in (
                        "topResumedActivity",
                        "mResumedActivity",
                        "mCurrentFocus",
                        "mFocusedApp",
                    )
                ):
                    continue
                match = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)/", line)
                if match:
                    return match.group(1)
        raise AdbProviderError(
            "foreground_app_unknown", "No foreground application could be identified"
        )


def parse_adb_devices(output: str) -> tuple[AdbDevice, ...]:
    devices: list[AdbDevice] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("List of devices attached") or line.startswith("*"):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        metadata = {
            key: value
            for field in fields[2:]
            if ":" in field
            for key, value in (field.split(":", 1),)
        }
        devices.append(
            AdbDevice(
                serial=fields[0],
                state=_device_state(fields[1]),
                model=metadata.get("model"),
                product=metadata.get("product"),
                transport_id=metadata.get("transport_id"),
            )
        )
    return tuple(devices)


def _device_state(value: str) -> AdbDeviceState:
    try:
        return AdbDeviceState(value)
    except ValueError:
        return AdbDeviceState.OTHER


def _state_error_code(state: AdbDeviceState) -> str:
    if state is AdbDeviceState.UNAUTHORIZED:
        return "adb_device_unauthorized"
    if state is AdbDeviceState.OFFLINE:
        return "adb_device_offline"
    return "adb_device_unavailable"


def _run_command(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="latin-1",
        timeout=timeout,
        check=False,
    )


def _bounded(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ")[:512]
