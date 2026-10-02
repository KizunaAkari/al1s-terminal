from __future__ import annotations

import io
import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import IO, Any

import pytest

from al1s_terminal.providers.mp4_validation import validate_recording_container
from al1s_terminal.providers.recorder import (
    AndroidScreenRecorder,
    RecordingProcess,
    ScreenRecordingError,
)


def mp4_box(kind: bytes, payload: bytes = b"test") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


MP4 = mp4_box(b"ftyp") + mp4_box(b"mdat") + mp4_box(b"moov")


class FakeRecordingProcess:
    def __init__(self) -> None:
        self.stderr: IO[bytes] | None = io.BytesIO()
        self._stopped = False

    def poll(self) -> int | None:
        return 0 if self._stopped else None

    def send_signal(self, _signal_number: int) -> None:
        self._stopped = True

    def wait(self, timeout: float | None = None) -> int:
        assert timeout is None or timeout > 0
        self._stopped = True
        return 0

    def kill(self) -> None:
        self._stopped = True


def test_screen_recorder_uses_explicit_serial_and_returns_pulled_mp4(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []
    process = FakeRecordingProcess()

    def run_text(command: Any, _timeout: float) -> subprocess.CompletedProcess[str]:
        values = tuple(str(item) for item in command)
        commands.append(values)
        if values[3] == "pull":
            assert process.poll() is not None
            Path(values[-1]).write_bytes(MP4)
        if values[3:5] == ("shell", "pidof"):
            return subprocess.CompletedProcess(values, 0, "42 43", "")
        if values[-1] == "/proc/42/cmdline":
            return subprocess.CompletedProcess(values, 0, "screenrecord\0/other.mp4\0", "")
        if values[-1] == "/proc/43/cmdline":
            return subprocess.CompletedProcess(
                values, 0, "screenrecord\0/sdcard/Download/al1s-taskinvalidid-001.mp4\0", ""
            )
        if values[3:5] == ("shell", "kill"):
            assert values[-2:] == ("-2", "43")
            process.send_signal(2)
        return subprocess.CompletedProcess(values, 0, "", "")

    def start_process(_command: Sequence[str]) -> RecordingProcess:
        return process

    recorder = AndroidScreenRecorder(
        adb_path="/opt/android/adb",
        data_dir=tmp_path,
        command_runner=run_text,
        process_factory=start_process,
        monotonic=lambda: 10.0,
    )

    session = recorder.start(serial="phone-1", task_id="task/invalid:id")
    result = recorder.stop(session)

    segments = result["segments"]
    assert isinstance(segments, list)
    assert len(segments) == 1
    assert Path(segments[0]["path"]).read_bytes() == MP4
    assert segments[0]["mime"] == "video/mp4"
    assert commands
    assert all(command[:3] == ("/opt/android/adb", "-s", "phone-1") for command in commands)


def test_screen_recorder_reports_process_start_failure_without_hanging(tmp_path: Path) -> None:
    def run_text(command: Any, _timeout: float) -> subprocess.CompletedProcess[str]:
        values = tuple(str(item) for item in command)
        return subprocess.CompletedProcess(values, 0, "", "")

    def fail_start(_command: Sequence[str]) -> RecordingProcess:
        raise OSError("screenrecord unavailable")

    recorder = AndroidScreenRecorder(
        adb_path="adb",
        data_dir=tmp_path,
        command_runner=run_text,
        process_factory=fail_start,
    )

    with pytest.raises(ScreenRecordingError, match="screenrecord unavailable"):
        recorder.start(serial="phone-1", task_id="task-1")


@pytest.mark.parametrize(
    "content",
    [
        mp4_box(b"ftyp") + mp4_box(b"mdat"),
        MP4[:-1],
        b"not an mp4 file at all",
        b"",
    ],
)
def test_unfinished_recording_is_rejected(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "video.mp4"
    path.write_bytes(content)
    with pytest.raises(ValueError, match="recording MP4"):
        validate_recording_container(path)


def test_remote_stop_does_not_signal_unrelated_process(tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    def run(command: Sequence[str], _timeout: float) -> subprocess.CompletedProcess[str]:
        commands.append(tuple(command))
        output = "42" if "pidof" in command else "screenrecord\0/other.mp4\0"
        return subprocess.CompletedProcess(command, 0, output, "")

    recorder = AndroidScreenRecorder(adb_path="adb", data_dir=tmp_path, command_runner=run)
    with pytest.raises(ScreenRecordingError, match="owner could not be verified"):
        recorder._finish_remote_recording("phone-1", "/expected.mp4", FakeRecordingProcess())
    assert not any("kill" in command for command in commands)


def test_remote_stop_timeout_is_not_treated_as_completed(tmp_path: Path) -> None:
    class SlowProcess(FakeRecordingProcess):
        def wait(self, timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired("adb", timeout or 0)

    def run(command: Sequence[str], _timeout: float) -> subprocess.CompletedProcess[str]:
        output = "42" if "pidof" in command else "screenrecord\0/expected.mp4\0"
        return subprocess.CompletedProcess(command, 0, output, "")

    recorder = AndroidScreenRecorder(adb_path="adb", data_dir=tmp_path, command_runner=run)
    with pytest.raises(subprocess.TimeoutExpired):
        recorder._finish_remote_recording("phone-1", "/expected.mp4", SlowProcess())


def test_mp4_extended_and_to_end_sizes(tmp_path: Path) -> None:
    path = tmp_path / "extended.mp4"
    path.write_bytes(
        mp4_box(b"ftyp")
        + (1).to_bytes(4, "big")
        + b"moov"
        + (20).to_bytes(8, "big")
        + b"test"
        + bytes(4)
        + b"mdat"
        + b"video"
    )
    validate_recording_container(path)


@pytest.mark.parametrize("pull_code,content", [(1, b""), (0, b"incomplete-video-data")])
def test_failed_pull_or_validation_preserves_phone_copy(
    tmp_path: Path, pull_code: int, content: bytes
) -> None:
    commands: list[tuple[str, ...]] = []

    def run(command: Sequence[str], _timeout: float) -> subprocess.CompletedProcess[str]:
        commands.append(tuple(command))
        if command[3] == "pull":
            Path(command[-1]).write_bytes(content)
            return subprocess.CompletedProcess(command, pull_code, "", "pull failed")
        return subprocess.CompletedProcess(command, 0, "", "")

    process = FakeRecordingProcess()
    process.kill()  # Segment reached its time limit before stop was requested.
    recorder = AndroidScreenRecorder(
        adb_path="adb",
        data_dir=tmp_path,
        command_runner=run,
        process_factory=lambda _command: process,
    )
    try:
        session = recorder.start(serial="phone-1", task_id="failed-pull")
    except ScreenRecordingError:
        pass  # The recording thread may already have reported the failure.
    else:
        with pytest.raises(ScreenRecordingError):
            recorder.stop(session)
    removals = [command for command in commands if command[3:6] == ("shell", "rm", "-f")]
    # Each new segment has initial cleanup; no segment is deleted a second time.
    assert removals
    assert len({command[-1] for command in removals}) == len(removals)


def test_next_segment_starts_while_previous_pull_is_blocked(tmp_path: Path) -> None:
    second_started, pulling, release = threading.Event(), threading.Event(), threading.Event()
    processes = []

    def start(command):
        process = FakeRecordingProcess()
        processes.append(process)
        if len(processes) == 1:
            process.kill()
        else:
            second_started.set()
        return process

    def run(command, timeout):
        if command[3] == "pull":
            pulling.set()
            assert release.wait(3)
            Path(command[-1]).write_bytes(MP4)
        return subprocess.CompletedProcess(command, 0, "", "")

    recorder = AndroidScreenRecorder(
        adb_path="adb", data_dir=tmp_path, command_runner=run, process_factory=start
    )
    session = recorder.start(serial="phone", task_id="slow-pull")
    try:
        assert pulling.wait(1)
        assert second_started.wait(1)
    finally:
        session.stop_event.set()
        for process in processes:
            process.kill()
        release.set()
        result = recorder.stop(session)
    assert result["segment_count"] == 2
