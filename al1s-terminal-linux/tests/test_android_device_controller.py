from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from al1s_terminal.providers.android_device import AndroidDeviceController

PNG = b"\x89PNG\r\n\x1a\ncontent"


def test_android_controller_scopes_every_command_to_bound_serial(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    def run_text(command: Any, _timeout: float) -> subprocess.CompletedProcess[str]:
        values = tuple(str(item) for item in command)
        commands.append(values)
        stdout = "device\n" if values[-1] == "get-state" else ""
        return subprocess.CompletedProcess(values, 0, stdout, "")

    controller = AndroidDeviceController(
        serial="phone-1",
        adb_path="/opt/android/adb",
        workdir=tmp_path,
        text_runner=run_text,
    )

    assert controller.start_session()["serial"] == "phone-1"
    assert controller.tap(12, 34) == {"x": 12, "y": 34}
    controller.force_stop_and_home("com.example.game")

    assert commands
    assert all(command[:3] == ("/opt/android/adb", "-s", "phone-1") for command in commands)


def test_android_controller_screenshot_is_atomic_and_keeps_transient_bytes_in_memory(
    tmp_path: Path,
) -> None:
    def run_binary(command: Any, _timeout: float) -> subprocess.CompletedProcess[bytes]:
        values = tuple(str(item) for item in command)
        return subprocess.CompletedProcess(values, 0, PNG, b"")

    controller = AndroidDeviceController(
        serial="phone-1",
        adb_path="adb",
        workdir=tmp_path,
        binary_runner=run_binary,
    )

    result = controller.screenshot()

    assert (tmp_path / "latest-screen.png").read_bytes() == PNG
    assert result["size_bytes"] == len(PNG)
    assert result["data_base64"]
    assert list(tmp_path.glob(".latest-screen-*.png")) == []


def test_android_controller_evidence_capture_is_unique_and_immutable(tmp_path: Path) -> None:
    def run_binary(command: Any, _timeout: float) -> subprocess.CompletedProcess[bytes]:
        values = tuple(str(item) for item in command)
        return subprocess.CompletedProcess(values, 0, PNG, b"")

    controller = AndroidDeviceController(
        serial="phone-1",
        adb_path="adb",
        workdir=tmp_path,
        binary_runner=run_binary,
    )

    first = controller.capture_evidence()
    second = controller.capture_evidence()

    first_path = Path(first["path"])
    second_path = Path(second["path"])
    assert first_path != second_path
    assert first_path.read_bytes() == PNG
    assert second_path.read_bytes() == PNG
    assert first_path.parent == tmp_path / "evidence"
    assert list((tmp_path / "evidence").glob(".*.tmp")) == []
