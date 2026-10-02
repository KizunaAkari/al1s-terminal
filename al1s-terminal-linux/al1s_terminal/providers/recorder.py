from __future__ import annotations

import os
import queue
import re
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Protocol

from al1s_terminal.providers.mp4_validation import validate_recording_container


class ScreenRecordingError(RuntimeError):
    pass


class RecordingProcess(Protocol):
    stderr: IO[bytes] | None

    def poll(self) -> int | None: ...

    def send_signal(self, signal_number: int) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


class ScreenRecorder(Protocol):
    def start(
        self,
        *,
        serial: str,
        task_id: str,
        segment_seconds: int = 180,
    ) -> RecordingSession: ...

    def stop(self, session: RecordingSession) -> dict[str, object]: ...


CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]
ProcessFactory = Callable[[Sequence[str]], RecordingProcess]


@dataclass(slots=True)
class RecordingSession:
    safe_id: str
    serial: str
    directory: Path
    segment_seconds: int
    started_monotonic: float
    stop_event: threading.Event = field(default_factory=threading.Event)
    started_event: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    segments: list[Path] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    current_process: RecordingProcess | None = None
    thread: threading.Thread | None = None


class AndroidScreenRecorder:
    def __init__(
        self,
        *,
        adb_path: str | Path,
        data_dir: Path,
        command_runner: CommandRunner | None = None,
        process_factory: ProcessFactory | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._adb_path = str(adb_path)
        self._recording_root = data_dir.resolve() / "runtime" / "recordings"
        self._command_runner = command_runner or _run_text
        self._process_factory = process_factory or _start_process
        self._monotonic = monotonic

    def start(
        self,
        *,
        serial: str,
        task_id: str,
        segment_seconds: int = 180,
    ) -> RecordingSession:
        if not serial.strip():
            raise ValueError("recording requires an explicit ADB serial")
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "", task_id)[:64]
        if not safe_id:
            raise ValueError("recording task id is invalid")
        directory = self._recording_root / safe_id
        directory.mkdir(parents=True, exist_ok=True)
        session = RecordingSession(
            safe_id=safe_id,
            serial=serial.strip(),
            directory=directory,
            segment_seconds=max(1, min(int(segment_seconds), 180)),
            started_monotonic=self._monotonic(),
        )
        thread = threading.Thread(
            target=self._recording_loop,
            args=(session,),
            daemon=True,
            name=f"screenrecord-{safe_id[:16]}",
        )
        session.thread = thread
        thread.start()
        if not session.started_event.wait(5):
            session.stop_event.set()
            raise ScreenRecordingError("Android screenrecord start timed out")
        if session.errors and session.current_process is None:
            raise ScreenRecordingError(session.errors[-1])
        return session

    def stop(self, session: RecordingSession) -> dict[str, object]:
        session.stop_event.set()
        thread = session.thread
        if thread is None:
            raise ScreenRecordingError("recording session was not started")
        # At most four queued segments plus the current transfer (55s each).
        thread.join(timeout=300)
        if thread.is_alive():
            with session.lock:
                process = session.current_process
            if process is not None and process.poll() is None:
                process.kill()
            thread.join(timeout=5)
        if thread.is_alive():
            raise ScreenRecordingError("Android screenrecord cleanup timed out")
        paths = [path for path in session.segments if path.is_file()]
        if not paths:
            raise ScreenRecordingError("; ".join(session.errors) or "Android recording is empty")
        return {
            "status": "captured",
            "segments": [
                {
                    "path": str(path),
                    "mime": "video/mp4",
                    "size_bytes": path.stat().st_size,
                    "segment_index": index,
                }
                for index, path in enumerate(paths, start=1)
            ],
            "segment_count": len(paths),
            "duration_seconds": round(self._monotonic() - session.started_monotonic, 3),
            "errors": list(session.errors),
        }

    def _recording_loop(self, session: RecordingSession) -> None:
        pending: queue.Queue[tuple[str, Path, str] | None] = queue.Queue(maxsize=4)

        def transfer() -> None:
            while True:
                item = pending.get()
                try:
                    if item is None:
                        return
                    remote, local, error = item
                    self._pull_segment(session, remote, local, error)
                except Exception as exc:
                    session.errors.append(str(exc)[:512])
                    session.stop_event.set()
                finally:
                    pending.task_done()

        consumer = threading.Thread(target=transfer, daemon=True, name="screenrecord-pull")
        consumer.start()
        try:
            self._capture_segments(session, pending)
        finally:
            pending.put(None)
            consumer.join()

    def _pull_segment(
        self, session: RecordingSession, remote: str, local: Path, error: str
    ) -> None:
        pull = self._adb(session.serial, 45, "pull", remote, str(local))
        if pull.returncode or not local.is_file() or local.stat().st_size < 16:
            raise ScreenRecordingError(
                pull.stderr.strip() or error or "Android recording segment is empty"
            )
        validate_recording_container(local)
        with local.open("r+b") as captured:
            os.fsync(captured.fileno())
        session.segments.append(local)
        self._adb(session.serial, 10, "shell", "rm", "-f", remote)

    def _capture_segments(
        self,
        session: RecordingSession,
        pending: queue.Queue[tuple[str, Path, str] | None],
    ) -> None:
        segment_index = 0
        while not session.stop_event.is_set():
            segment_index += 1
            remote = f"/sdcard/Download/al1s-{session.safe_id}-{segment_index:03d}.mp4"
            local = session.directory / f"{session.safe_id}-{segment_index:03d}.mp4"
            command = self._command(
                session.serial,
                "shell",
                "screenrecord",
                "--bit-rate",
                "4000000",
                "--time-limit",
                str(session.segment_seconds),
                remote,
            )
            try:
                self._adb(session.serial, 10, "shell", "rm", "-f", remote)
                process = self._process_factory(command)
                with session.lock:
                    session.current_process = process
                session.started_event.set()
                while process.poll() is None and not session.stop_event.wait(0.25):
                    pass
                if process.poll() is None:
                    self._finish_remote_recording(session.serial, remote, process)
                error = (
                    process.stderr.read().decode(errors="replace").strip()
                    if process.stderr is not None
                    else ""
                )
                # Restart capture without waiting for ADB pull or local validation.
                # Overflow fails explicitly and retains the remote file; no silent loss.
                try:
                    pending.put_nowait((remote, local, error))
                except queue.Full as exc:
                    raise ScreenRecordingError(
                        "recording transfer backlog exceeded; remote segment retained"
                    ) from exc
            except Exception as exc:
                session.errors.append(str(exc)[:512])
                session.started_event.set()
                with session.lock:
                    failed_process = session.current_process
                if failed_process is not None and failed_process.poll() is None:
                    failed_process.kill()
                    failed_process.wait(timeout=5)
                break
            finally:
                with session.lock:
                    session.current_process = None

    def _finish_remote_recording(self, serial: str, remote: str, process: RecordingProcess) -> None:
        # Stopping the host adb can disconnect before MediaMuxer writes moov.
        # Only signal a phone process whose output is this exact segment.
        listing = self._adb(serial, 5, "shell", "pidof", "screenrecord")
        pids = listing.stdout.split()
        if len(pids) > 8 or any(not pid.isascii() or not pid.isdigit() for pid in pids):
            raise ScreenRecordingError("cannot safely identify Android screenrecord")
        for pid in pids:
            command = self._adb(serial, 5, "shell", "cat", f"/proc/{pid}/cmdline")
            arguments = command.stdout.rstrip("\0").split("\0")
            if (
                command.returncode == 0
                and arguments
                and arguments[0].rsplit("/", 1)[-1] == "screenrecord"
                and arguments[-1] == remote
            ):
                stopped = self._adb(serial, 5, "shell", "kill", "-2", pid)
                if stopped.returncode:
                    raise ScreenRecordingError("Android screenrecord stop was rejected")
                break
        else:
            if process.poll() is None:
                raise ScreenRecordingError("Android screenrecord owner could not be verified")
        process.wait(timeout=10)

    def _adb(
        self,
        serial: str,
        timeout: float,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        return self._command_runner(self._command(serial, *arguments), timeout)

    def _command(self, serial: str, *arguments: str) -> tuple[str, ...]:
        return (self._adb_path, "-s", serial, *arguments)


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


def _start_process(command: Sequence[str]) -> RecordingProcess:
    return subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
