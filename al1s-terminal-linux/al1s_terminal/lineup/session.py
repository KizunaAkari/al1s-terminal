"""One cancellable local inference process, reused only between serial requests."""

import atexit
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from al1s_terminal.execution.maa_runtime import MaaExecutionOutcome


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


class InferenceSession:
    def __init__(self, assets: Path):
        self.assets = assets.resolve()
        self._process: subprocess.Popen[bytes] | None = None
        self._directory: tempfile.TemporaryDirectory[str] | None = None
        self._lock = threading.Lock()
        self._registered = False
        self._last_response = 0.0

    def close(self) -> None:
        with self._lock:
            self._discard()

    def _discard(self) -> None:
        process, self._process = self._process, None
        if process is not None:
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    if sys.platform != "win32":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
            process.wait(timeout=5)
        if self._directory is not None:
            self._directory.cleanup()
            self._directory = None

    def _start(self) -> Path:
        if (
            self._process is None
            or self._process.poll() is not None
            or time.monotonic() - self._last_response >= 50
        ):
            self._discard()
            self._directory = tempfile.TemporaryDirectory(prefix="al1s-lineup-")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = os.pathsep.join(
                filter(
                    None,
                    (
                        str(Path(__file__).resolve().parents[2]),
                        environment.get("PYTHONPATH", ""),
                    ),
                )
            )
            self._process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "al1s_terminal.lineup.server",
                    str(self.assets),
                    self._directory.name,
                    str(os.getpid()),
                ],
                cwd=self._directory.name,
                env=environment,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if not self._registered:
                atexit.register(self.close)
                self._registered = True
        assert self._directory is not None
        return Path(self._directory.name)

    def execute(
        self, source: Path, hint: str, mode: str, timeout: int, cancelled: Callable[[], bool]
    ) -> MaaExecutionOutcome:
        deadline = time.monotonic() + timeout
        while not self._lock.acquire(timeout=0.05):
            if cancelled():
                return MaaExecutionOutcome(False, "execution_cancelled", {})
            if time.monotonic() >= deadline:
                return MaaExecutionOutcome(False, "lineup_timeout", {})
        try:
            if cancelled():
                return MaaExecutionOutcome(False, "execution_cancelled", {})
            if time.monotonic() >= deadline:
                return MaaExecutionOutcome(False, "lineup_timeout", {})
            directory = self._start()
            request_id = uuid4().hex
            response = directory / "response.json"
            response.unlink(missing_ok=True)
            atomic_json(
                directory / "request.json",
                {
                    "id": request_id,
                    "image": str(source.resolve()),
                    "hint": hint,
                    "mode": mode,
                },
            )
            return self._await_response(response, request_id, deadline, cancelled)
        except Exception:
            self._discard()
            return MaaExecutionOutcome(False, "lineup_inference_failed", {})
        finally:
            self._lock.release()

    def _await_response(
        self, path: Path, request_id: str, deadline: float, cancelled: Callable[[], bool]
    ) -> MaaExecutionOutcome:
        while True:
            error = (
                "execution_cancelled"
                if cancelled()
                else "lineup_timeout"
                if time.monotonic() >= deadline
                else None
            )
            if error:
                self._discard()
                return MaaExecutionOutcome(False, error, {})
            if path.is_file():
                if path.stat().st_size > 65536:
                    raise ValueError("lineup_response_too_large")
                value = json.loads(path.read_text(encoding="utf-8"))
                if value.get("id") != request_id or not isinstance(value.get("result"), dict):
                    raise ValueError("lineup_response_invalid")
                self._last_response = time.monotonic()
                return MaaExecutionOutcome(True, None, {"lineup": value["result"]})
            if self._process is None or self._process.poll() is not None:
                raise RuntimeError("lineup_worker_exited")
            time.sleep(0.1)
