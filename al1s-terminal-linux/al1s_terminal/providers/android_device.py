from __future__ import annotations

import base64
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

TextRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]
BinaryRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[bytes]]


class AndroidDeviceController:
    """Bound-device ADB operations used by registered Maa actions.

    The serial is mandatory. Device discovery and selection belong to the
    explicit platform binding service and are never repeated in this provider.
    """

    def __init__(
        self,
        *,
        serial: str,
        adb_path: str | Path,
        workdir: Path,
        text_runner: TextRunner | None = None,
        binary_runner: BinaryRunner | None = None,
    ) -> None:
        if not serial.strip():
            raise ValueError("Android device serial is required")
        self.serial = serial.strip()
        self.adb_path = str(adb_path)
        self.workdir = workdir
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._text_runner = text_runner or _run_text
        self._binary_runner = binary_runner or _run_binary

    def start_session(self) -> dict[str, Any]:
        self._shell("input", "keyevent", "KEYCODE_WAKEUP")
        self._command("shell", "wm", "dismiss-keyguard", timeout=10)
        self._shell("input", "keyevent", "KEYCODE_HOME")
        state = self._command("get-state", timeout=10)
        if state.stdout.strip() != "device":
            raise RuntimeError("Android device disconnected while starting session")
        return {"accepted": True, "serial": self.serial, "home": True}

    def screenshot(self) -> dict[str, Any]:
        command = self._base_command("exec-out", "screencap", "-p")
        result = self._binary_runner(command, 30.0)
        if result.returncode != 0 or not result.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
            diagnostic = result.stderr.decode(errors="replace").strip()
            raise RuntimeError(diagnostic or "ADB screenshot did not return a PNG")
        target = self.workdir / "latest-screen.png"
        temporary = self.workdir / f".latest-screen-{time.time_ns()}.png"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(result.stdout)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "path": str(target),
            "mime": "image/png",
            "size_bytes": len(result.stdout),
            # Some verified image algorithms require the current frame in-memory.
            # It is never persisted in SQLite or transported in a report payload.
            "data_base64": base64.b64encode(result.stdout).decode("ascii"),
        }

    def capture_evidence(self) -> dict[str, Any]:
        capture = self.screenshot()
        payload = base64.b64decode(str(capture["data_base64"]), validate=True)
        evidence_dir = self.workdir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        target = evidence_dir / f"{uuid4()}.png"
        temporary = evidence_dir / f".{target.stem}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return {**capture, "path": str(target)}

    def tap(self, x: int, y: int) -> dict[str, int]:
        self._shell("input", "tap", str(x), str(y))
        return {"x": x, "y": y}

    def force_stop_and_home(self, package_name: str | None) -> None:
        if package_name:
            self._shell("am", "force-stop", package_name)
        self._shell("input", "keyevent", "KEYCODE_HOME")

    def _shell(self, *args: str) -> str:
        return self._command("shell", *args, timeout=30).stdout

    def _command(self, *args: str, timeout: float) -> subprocess.CompletedProcess[str]:
        result = self._text_runner(self._base_command(*args), timeout)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"ADB command failed: {' '.join(args)}")
        return result

    def _base_command(self, *args: str) -> tuple[str, ...]:
        return (self.adb_path, "-s", self.serial, *args)


def _run_text(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _run_binary(command: Sequence[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
