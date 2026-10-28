from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import signal
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import Path
from threading import Lock
from typing import Any

from al1s_terminal.execution.maa_adapter import MaaAdapter, MaaExecutionError, YoloAdapter
from al1s_terminal.execution.maa_pipeline import CompiledMaaTask
from al1s_terminal.execution.maa_runtime import MaaRuntimeError
from al1s_terminal.providers.android_device import AndroidDeviceController

MAX_DIAGNOSTIC_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class MaaWorkerRequest:
    task: CompiledMaaTask
    adb_serial: str
    adb_path: str
    workdir: Path
    ocr_model_dir: Path | None
    yolo_provider_spec: str | None
    adb_screencap_methods: int | None
    adb_input_methods: int | None
    screenshot_mode: str
    screenshot_short_side: int
    adb_command_timeout_seconds: int
    capture_failure: bool
    emit_events: bool = False


@dataclass(frozen=True, slots=True)
class MaaWorkerReply:
    succeeded: bool
    result: dict[str, Any]
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class MaaWorkerEvent:
    kind: str
    step_number: int


WorkerTarget = Callable[[Connection, MaaWorkerRequest], None]


class MaaFrameworkTaskRunner:
    """Run one compiled Maa module in a killable child process.

    MaaFramework and custom OpenCV/RKNN extensions are native, potentially
    blocking workloads. A process boundary makes the product-level timeout a
    real upper bound and prevents a timed-out native task from being reused by
    the next work item.
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        adb_path: str | Path,
        ocr_model_dir: Path | None,
        yolo_provider_spec: str | None = None,
        adb_screencap_methods: int | None = None,
        adb_input_methods: int | None = None,
        screenshot_mode: str = "raw",
        screenshot_short_side: int = 720,
        adb_command_timeout_seconds: int = 30,
        start_method: str = "spawn",
        worker_target: WorkerTarget | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._adb_path = str(adb_path)
        self._ocr_model_dir = ocr_model_dir
        self._yolo_provider_spec = yolo_provider_spec
        self._adb_screencap_methods = adb_screencap_methods
        self._adb_input_methods = adb_input_methods
        self._screenshot_mode = screenshot_mode
        self._screenshot_short_side = screenshot_short_side
        self._adb_command_timeout_seconds = adb_command_timeout_seconds
        self._context: Any = get_context(start_method)
        self._worker_target = worker_target or _run_maa_task

    def run(
        self,
        task: CompiledMaaTask,
        *,
        adb_serial: str,
        timeout_seconds: int,
        capture_failure: bool = False,
        cancel_requested: Callable[[], bool] | None = None,
        on_event: Callable[[str, int], None] | None = None,
    ) -> dict[str, Any]:
        if timeout_seconds < 1:
            raise MaaRuntimeError("execution_timeout", "Maa execution timed out")
        workdir = self._data_dir / "runtime" / _serial_directory(adb_serial)
        request = MaaWorkerRequest(
            task=task,
            adb_serial=adb_serial,
            adb_path=self._adb_path,
            workdir=workdir,
            ocr_model_dir=self._ocr_model_dir,
            yolo_provider_spec=self._yolo_provider_spec,
            adb_screencap_methods=self._adb_screencap_methods,
            adb_input_methods=self._adb_input_methods,
            screenshot_mode=self._screenshot_mode,
            screenshot_short_side=self._screenshot_short_side,
            adb_command_timeout_seconds=self._adb_command_timeout_seconds,
            capture_failure=capture_failure,
            emit_events=on_event is not None,
        )
        receive_end, send_end = self._context.Pipe(duplex=False)
        process = self._context.Process(
            target=_run_isolated_worker,
            args=(self._worker_target, send_end, request),
            name=f"maa-{task.script_hash[:12]}",
            daemon=True,
        )
        process.start()
        send_end.close()
        deadline = time.monotonic() + timeout_seconds
        reply: MaaWorkerReply | None = None
        while process.is_alive() and time.monotonic() < deadline:
            # Drain a bounded result while the child is alive: waiting for exit
            # first deadlocks when evidence metadata exceeds the pipe buffer.
            if reply is None and receive_end.poll():
                try:
                    received = receive_end.recv()
                    if isinstance(received, MaaWorkerReply):
                        reply = received
                    elif isinstance(received, MaaWorkerEvent) and on_event is not None:
                        with suppress(Exception):
                            on_event(received.kind, received.step_number)
                except EOFError:
                    pass
            if cancel_requested is not None and cancel_requested():
                self._terminate(process)
                receive_end.close()
                raise MaaRuntimeError(
                    "execution_cancelled",
                    "Maa execution was cancelled",
                    {"script_hash": task.script_hash},
                )
            process.join(min(0.25, max(0.0, deadline - time.monotonic())))
        if process.is_alive():
            self._terminate(process)
            receive_end.close()
            raise MaaRuntimeError(
                "execution_timeout",
                "Maa execution timed out",
                {"script_hash": task.script_hash},
            )
        while reply is None and receive_end.poll():
            try:
                received = receive_end.recv()
            except EOFError:
                break
            if isinstance(received, MaaWorkerReply):
                reply = received
            elif isinstance(received, MaaWorkerEvent) and on_event is not None:
                with suppress(Exception):
                    on_event(received.kind, received.step_number)
        receive_end.close()
        if reply is None:
            raise MaaRuntimeError(
                "maa_worker_crashed",
                "Maa execution worker exited without a result",
                {
                    "exit_code": process.exitcode,
                    "script_hash": task.script_hash,
                },
            )
        if not reply.succeeded:
            raise MaaRuntimeError(
                reply.error_code or "maa_pipeline_failed",
                str(reply.result.get("error") or "MaaFramework pipeline execution failed")[:512],
                reply.result,
            )
        return reply.result

    @staticmethod
    def _terminate(process: Any) -> None:
        process_group = _isolated_process_group(process)
        if process_group is None:
            process.terminate()
        else:
            with suppress(ProcessLookupError):
                _kill_process_group(process_group, int(signal.SIGTERM))
        process.join(10)
        # ADB children can survive after the worker handles TERM and exits.
        if process.is_alive() or process_group is not None:
            if process_group is None:
                process.kill()
            else:
                with suppress(ProcessLookupError):
                    _kill_process_group(process_group, int(_platform_signal().SIGKILL))
            process.join(5)


def _run_isolated_worker(
    target: WorkerTarget,
    send_end: Connection,
    request: MaaWorkerRequest,
) -> None:
    if os.name == "posix":
        _platform_os().setsid()
    target(send_end, request)


def _isolated_process_group(process: Any) -> int | None:
    if os.name != "posix" or process.pid is None:
        return None
    try:
        process_group = int(_platform_os().getpgid(process.pid))
    except ProcessLookupError:
        return None
    return process_group if process_group == process.pid else None


def _kill_process_group(process_group: int, signal_number: int) -> None:
    _platform_os().killpg(process_group, signal_number)


def _platform_os() -> Any:
    """Expose POSIX-only functions without breaking Windows type checking."""
    return os


def _platform_signal() -> Any:
    return signal


def _run_maa_task(send_end: Connection, request: MaaWorkerRequest) -> None:
    event_lock = Lock()
    def emit_event(kind: str, step_number: int) -> None:
        with event_lock:
            send_end.send(MaaWorkerEvent(kind, step_number))

    try:
        device = AndroidDeviceController(
            serial=request.adb_serial,
            adb_path=request.adb_path,
            workdir=request.workdir,
        )
        adapter = MaaAdapter(
            device,
            YoloAdapter(request.yolo_provider_spec),
            adb_path=_bounded_adb_executable(request),
            ocr_model_dir=request.ocr_model_dir,
            adb_screencap_methods=request.adb_screencap_methods,
            adb_input_methods=request.adb_input_methods,
            screenshot_mode=request.screenshot_mode,
            screenshot_short_side=request.screenshot_short_side,
        )
        result = adapter.run(
            request.task.source_script,
            {},
            compiled_task=request.task,
            on_step_event=emit_event if request.emit_events else None,
        )
        send_end.send(MaaWorkerReply(True, _bounded_diagnostic(result)))
    except MaaExecutionError as exc:
        diagnostic = dict(exc.execution_result)
        if request.capture_failure:
            try:
                diagnostic["failure_screenshot"] = device.capture_evidence()
                adapter.enrich_failure_diagnosis(diagnostic, request.task.source_script)
            except Exception as capture_exc:
                diagnostic["failure_screenshot_error"] = str(capture_exc)[:512]
        diagnostic = _bounded_diagnostic(diagnostic)
        send_end.send(
            MaaWorkerReply(
                False,
                diagnostic,
                _runtime_error_code(str(diagnostic.get("error_type") or "")),
            )
        )
    except Exception as exc:
        send_end.send(
            MaaWorkerReply(
                False,
                {
                    "error": str(exc)[:512],
                    "error_type": type(exc).__name__,
                },
                "maa_runtime_failed",
            )
        )
    finally:
        send_end.close()


def _bounded_adb_executable(request: MaaWorkerRequest) -> str:
    """Bound Maa's native ADB calls, which otherwise have no Python-side deadline."""
    if os.name != "posix":
        return request.adb_path
    timeout_binary = shutil.which("timeout")
    if timeout_binary is None:
        return request.adb_path
    wrapper_dir = request.workdir / "bin"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper = wrapper_dir / "adb-bounded"
    script = _bounded_adb_script(
        timeout_binary,
        request.adb_path,
        request.adb_command_timeout_seconds,
    )
    if not wrapper.exists() or wrapper.read_text(encoding="utf-8") != script:
        temporary = wrapper.with_name(f".{wrapper.name}-{time.time_ns()}")
        temporary.write_text(script, encoding="utf-8", newline="\n")
        temporary.chmod(0o700)
        os.replace(temporary, wrapper)
    return str(wrapper)


def _bounded_adb_script(timeout_binary: str, adb_path: str, timeout_seconds: int) -> str:
    adb = shlex.quote(adb_path)
    return (
        "#!/bin/sh\n"
        # Maa must never restart the shared server used by preview/other devices.
        'if [ "$1" = "kill-server" ]; then exit 0; fi\n'
        # Maa owns these streams until controller teardown; the worker process
        # group remains bounded by task timeout/cancellation.
        'if [ "$1" = "-s" ] && [ "$3" = "shell" ] && [ "$#" = 4 ]; then\n'
        '  case "$4" in\n'
        f'    cat|*"com.shxyke.MaaTouch.App"|*"/minitouch"*) exec {adb} "$@" ;;\n'
        '  esac\n'
        'fi\n'
        f"exec {shlex.quote(timeout_binary)} --foreground -k 5s "
        f'{timeout_seconds}s {adb} "$@"\n'
    )



def _runtime_error_code(error_type: str) -> str:
    return {
        "MaaFrameworkUnavailable": "maa_runtime_unavailable",
        "YoloProviderUnavailable": "yolo_runtime_unavailable",
        "MaaOcrModelUnavailable": "maa_ocr_model_unavailable",
        "AdbDeviceNotFound": "adb_device_missing",
        "AdbControllerConnectionFailed": "maa_adb_connection_failed",
        "MaaTaskerInitializationFailed": "maa_tasker_initialization_failed",
        "MaaExtensionRegistrationFailed": "maa_extension_registration_failed",
        "MaaControllerConfigurationInvalid": "maa_controller_configuration_invalid",
        "MaaScreenSizeMismatch": "maa_screen_size_mismatch",
        "MaaClickRepeatTimeout": "maa_click_repeat_timeout",
        "MaaRepeatedClickFailed": "maa_repeated_click_failed",
        "MaaIndependentRuleLimit": "maa_independent_rule_limit",
        "MaaIndependentRuleFailed": "maa_independent_rule_failed",
        "MaaIndependentRuleTimeout": "maa_independent_rule_timeout",
        "MaaStepBudgetFailed": "maa_step_budget_failed",
        "MaaStepExecutionStalled": "maa_step_execution_stalled",
        "PostAssertionFailed": "post_assertion_failed",
        "FailureRetryLimitExceeded": "failure_retry_limit_exceeded",
        "FailureRetryProcessFailed": "failure_retry_process_failed",
        "MatchLoopLimitExceeded": "match_loop_limit_exceeded",
        "CourseScheduleFailed": "course_schedule_failed",
    }.get(error_type, "maa_pipeline_failed")


def _bounded_diagnostic(value: dict[str, Any]) -> dict[str, Any]:
    captures = _capture_references(value)
    normalized = _json_value(value, depth=0)
    if not isinstance(normalized, dict):
        return {"detail": str(normalized)[:512]}
    encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) <= MAX_DIAGNOSTIC_BYTES:
        if captures:
            normalized["capture_files"] = captures
        return normalized
    return {
        "truncated": True,
        "error": str(normalized.get("error") or "")[:512] or None,
        "error_type": str(normalized.get("error_type") or "")[:100] or None,
        "failed_step": normalized.get("failed_step"),
        "failure_diagnosis": normalized.get("failure_diagnosis"),
        "recognition_failure": normalized.get("recognition_failure"),
        "execution_failure": normalized.get("execution_failure"),
        "failure_screenshot": normalized.get("failure_screenshot"),
        "failure_screenshot_error": normalized.get("failure_screenshot_error"),
        "capture_files": captures,
    }


def _capture_references(value: dict[str, Any]) -> list[dict[str, str]]:
    """Keep file ownership through log truncation, without image bytes."""
    found: dict[str, dict[str, str]] = {}
    pending: list[Any] = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            path, mime = item.get("path"), item.get("mime")
            if (isinstance(path, str) and len(path) <= 2048
                    and isinstance(mime, str) and mime.startswith("image/")):
                if path not in found and len(found) < 257:
                    found[path] = {"path": path, "mime": mime[:100]}
            else:
                pending.extend(item.values())
        elif isinstance(item, (tuple, list)):
            pending.extend(item)
    return list(found.values())


def _json_value(value: Any, *, depth: int) -> Any:
    if depth >= 8:
        return "<depth-limit>"
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return value[:2_048]
    if isinstance(value, dict):
        return {
            str(key)[:100]: _json_value(item, depth=depth + 1)
            for key, item in list(value.items())[:100]
            if key != "data_base64"
        }
    if isinstance(value, list | tuple):
        return [_json_value(item, depth=depth + 1) for item in value[:100]]
    return str(value)[:512]


def _serial_directory(serial: str) -> str:
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()[:20]
