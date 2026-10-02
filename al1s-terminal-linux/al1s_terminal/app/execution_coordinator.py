from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

import structlog

from al1s_terminal.app.device_discovery import TargetDeviceDiscoveryService
from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.execution.artifact_store import ExecutionArtifactCollector
from al1s_terminal.execution.maa_definition import MaaDefinitionError
from al1s_terminal.execution.maa_runtime import (
    MaaExecutionEngine,
    MaaExecutionOutcome,
    MaaRuntimeError,
    PreparedMaaExecution,
)
from al1s_terminal.execution.result_policy import RETRYABLE_SCRIPT_ERRORS, final_diagnostic
from al1s_terminal.execution.rule_event_codes import rule_event_code
from al1s_terminal.execution.work_item_loader import (
    ExecutionWorkError,
    ExecutionWorkLoader,
    LoadedExecutionWork,
)
from al1s_terminal.interactive.sessions import (
    InteractiveAutomationGate,
    InteractiveSessionError,
)
from al1s_terminal.lineup.executor import LineupExecutor
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.providers.adb import AdbProvider, AdbProviderError
from al1s_terminal.providers.recorder import (
    RecordingSession,
    ScreenRecorder,
    ScreenRecordingError,
)
from al1s_terminal.providers.resource_guard import RuntimeResourceGuard
from al1s_terminal.transport.delivery import DeliveryPlatformPort
from al1s_terminal.transport.delivery_models import AttemptStartPayload
from al1s_terminal.transport.platform import (
    PlatformCredentialRejectedError,
    PlatformError,
    PlatformUnavailableError,
)
from al1s_terminal.types import (
    OfflinePermitStatus,
    ReportKind,
    WorkItemKind,
    WorkItemRecord,
    WorkItemStatus,
)

START_REPORT_NAMESPACE = UUID("71126863-84d9-46a1-ac04-48332cb43197")
RESULT_REPORT_NAMESPACE = UUID("f10781be-7743-4276-a5ef-a29f6e6414a4")


@dataclass(frozen=True, slots=True)
class ExecutionCycleResult:
    disposition: str
    work_item_id: UUID | None = None
    error_code: str | None = None


class ExecutionCoordinator:
    def __init__(
        self,
        *,
        platform: DeliveryPlatformPort,
        secret_store: FileSecretStore,
        device_discovery: TargetDeviceDiscoveryService,
        adb: AdbProvider,
        loader: ExecutionWorkLoader,
        engine: MaaExecutionEngine,
        uow_factory: Callable[[], LocalUnitOfWork],
        executor_version: str,
        artifact_collector: ExecutionArtifactCollector | None = None,
        screen_recorder: ScreenRecorder | None = None,
        stop_requested: Callable[[], bool] | None = None,
        resource_guard: RuntimeResourceGuard | None = None,
        interactive_gate: InteractiveAutomationGate | None = None,
        lineup_executor: LineupExecutor | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._platform = platform
        self._secret_store = secret_store
        self._device_discovery = device_discovery
        self._adb = adb
        self._loader = loader
        self._engine = engine
        self._uow_factory = uow_factory
        self._executor_version = executor_version
        self._artifact_collector = artifact_collector
        self._screen_recorder = screen_recorder
        self._stop_requested = stop_requested or (lambda: False)
        self._resource_guard = resource_guard
        self._interactive_gate = interactive_gate
        self._lineup_executor = lineup_executor
        self._clock = clock or (lambda: datetime.now(UTC))
        self._quick_event_failures = 0
        self._quick_event_last_warning: datetime | None = None

    def run_once(self) -> ExecutionCycleResult:
        active = self._active()
        if active is not None:
            self._queue_interrupted(active)
            return ExecutionCycleResult(
                "interrupted_recovered", active.work_item_id, "execution_interrupted"
            )

        work_item = self._next_queued()
        if work_item is None:
            return ExecutionCycleResult("idle")
        if work_item.kind is WorkItemKind.QUICK_TEST:
            identity = self._secret_store.load()
            if identity is not None:
                try:
                    control = self._platform.get_quick_test_control(
                        identity.terminal_id, identity.credential, work_item.remote_id
                    )
                except PlatformUnavailableError:
                    control = None
                if control is not None and control.cancel_requested:
                    self._start_and_queue_failure(work_item, "execution_cancelled")
                    return ExecutionCycleResult(
                        "result_queued", work_item.work_item_id, "execution_cancelled"
                    )

        try:
            loaded = self._loader.load(work_item)
            if self._resource_guard is not None:
                resources = self._resource_guard.evaluate(
                    loaded.capability_requirements,
                    record_video=loaded.record_video,
                )
                if not resources.permitted:
                    return ExecutionCycleResult(
                        "resource_blocked", work_item.work_item_id, resources.code
                    )
            if loaded.local_manifest is None and loaded.plan is None:
                raise ExecutionWorkError("maa_definition_missing", "Maa definition is missing")
            prepared = self._engine.prepare(loaded.plan) if loaded.plan is not None else None
        except (ExecutionWorkError, MaaRuntimeError, MaaDefinitionError) as exc:
            try:
                self._start_and_queue_failure(
                    work_item, getattr(exc, "code", "maa_definition_invalid")
                )
            except PlatformError as platform_exc:
                return ExecutionCycleResult(
                    "start_deferred", work_item.work_item_id, platform_exc.code
                )
            return ExecutionCycleResult("failed_preflight", work_item.work_item_id, exc.code)
        except Exception as exc:
            try:
                self._start_and_queue_failure(work_item, "maa_definition_invalid")
            except PlatformError as platform_exc:
                return ExecutionCycleResult(
                    "start_deferred", work_item.work_item_id, platform_exc.code
                )
            return ExecutionCycleResult(
                "failed_preflight", work_item.work_item_id, type(exc).__name__
            )

        if loaded.local_manifest is not None:
            return self._execute_lineup(work_item, loaded)
        assert prepared is not None
        if loaded.expires_at is not None and loaded.expires_at <= self._clock():
            try:
                self._start_and_queue_failure(work_item, "quick_test_expired", loaded=loaded)
            except PlatformError as exc:
                return ExecutionCycleResult("start_deferred", work_item.work_item_id, exc.code)
            return ExecutionCycleResult(
                "failed_preflight", work_item.work_item_id, "quick_test_expired"
            )

        serial = self._live_serial(work_item)
        if serial is None:
            return ExecutionCycleResult("device_unavailable", work_item.work_item_id)

        gate_acquired = False
        try:
            if self._interactive_gate is not None:
                gate_acquired = True
                self._interactive_gate.begin_automation(serial)
            return self._execute_prepared(work_item, loaded, prepared, serial)
        except InteractiveSessionError as exc:
            return ExecutionCycleResult(
                "interactive_blocked",
                work_item.work_item_id,
                exc.code,
            )
        finally:
            if gate_acquired and self._interactive_gate is not None:
                self._interactive_gate.end_automation(serial)

    def _execute_prepared(
        self,
        work_item: WorkItemRecord,
        loaded: LoadedExecutionWork,
        prepared: PreparedMaaExecution,
        serial: str,
    ) -> ExecutionCycleResult:
        try:
            running = self._start(loaded)
        except PlatformCredentialRejectedError:
            raise
        except PlatformError as exc:
            return ExecutionCycleResult("start_deferred", work_item.work_item_id, exc.code)

        effective_timeout = loaded.timeout_seconds
        if loaded.expires_at is not None:
            effective_timeout = min(
                effective_timeout,
                max(1, int((loaded.expires_at - self._clock()).total_seconds())),
            )
        recording: RecordingSession | None = None
        recording_diagnostic: dict[str, Any] | None = None
        if loaded.record_video:
            if self._screen_recorder is None:
                recording_diagnostic = {
                    "status": "failed_to_start",
                    "error_code": "screen_recording_unavailable",
                }
            else:
                try:
                    recording = self._screen_recorder.start(
                        serial=serial,
                        task_id=str(running.work_item_id),
                    )
                except (OSError, ValueError, ScreenRecordingError) as exc:
                    recording_diagnostic = {
                        "status": "failed_to_start",
                        "error_code": type(exc).__name__,
                    }
        try:
            execution_options: dict[str, Any] = {
                "adb_serial": serial,
                "timeout_seconds": effective_timeout,
                "capture_failure": running.kind is WorkItemKind.FORMAL_TASK,
                "cancel_requested": lambda: (
                    self._stop_requested() or self._cancel_requested(running.work_item_id)
                ),
            }
            if running.kind is WorkItemKind.QUICK_TEST:
                try:
                    self._record_quick_event(running, "started", None)
                except Exception as exc:
                    self._warn_quick_event_failure(exc)
                    # Execution remains independent of debug telemetry.
                else:
                    execution_options["on_event"] = lambda kind, step: self._safe_quick_event(
                        running, kind,
                        loaded.debug_step_number if loaded.debug_step_number is not None
                        and step == 1 and kind.startswith(("step_", "failure_skipped:")) else step,
                    )
            outcome = self._engine.execute(prepared, **execution_options)
        except MaaRuntimeError as exc:
            outcome = MaaExecutionOutcome(False, exc.code, exc.diagnostic)
        except Exception as exc:
            outcome = MaaExecutionOutcome(
                False,
                "maa_runtime_failed",
                {"detail": str(exc)[:512]},
            )
        if running.kind is WorkItemKind.FORMAL_TASK:
            try:
                self._adb.force_stop_and_home(serial, _target_package(loaded))
            except AdbProviderError as exc:
                outcome = MaaExecutionOutcome(
                    False,
                    outcome.error_code or exc.code,
                    {
                        **outcome.diagnostic,
                        "cleanup": {"status": "failed", "error_code": exc.code},
                    },
                )
            else:
                outcome = MaaExecutionOutcome(
                    outcome.passed,
                    outcome.error_code,
                    {**outcome.diagnostic, "cleanup": {"status": "completed"}},
                )
        if recording is not None and self._screen_recorder is not None:
            try:
                recording_diagnostic = self._screen_recorder.stop(recording)
            except (OSError, ScreenRecordingError) as exc:
                recording_diagnostic = {
                    "status": "failed_to_stop",
                    "error_code": type(exc).__name__,
                }
        if recording_diagnostic is not None:
            outcome = MaaExecutionOutcome(
                outcome.passed,
                outcome.error_code,
                {**outcome.diagnostic, "recording": recording_diagnostic},
            )
        if self._artifact_collector is not None:
            outcome = self._artifact_collector.collect(loaded, outcome)
        if running.kind is WorkItemKind.QUICK_TEST and not outcome.passed:
            error_code = outcome.error_code or "maa_runtime_failed"
            if not error_code.isascii() or len(error_code) > 100:
                error_code = "maa_runtime_failed"
            with suppress(Exception), self._uow_factory() as uow:
                uow.quick_test_events.append(
                    running, kind="log", step_number=None,
                    code=error_code, now=self._clock(),
                )
        self._queue_result(running, loaded, outcome)
        return ExecutionCycleResult(
            "result_queued",
            work_item.work_item_id,
            outcome.error_code,
        )

    def _execute_lineup(self, work_item: WorkItemRecord,
                        loaded: LoadedExecutionWork) -> ExecutionCycleResult:
        try:
            running = self._start(loaded)
        except PlatformCredentialRejectedError:
            raise
        except PlatformError as exc:
            return ExecutionCycleResult('start_deferred', work_item.work_item_id, exc.code)
        try:
            if self._lineup_executor is None:
                outcome = MaaExecutionOutcome(False, 'lineup_executor_unavailable', {})
            else:
                outcome = self._lineup_executor.execute(
                    loaded.local_manifest or {}, loaded.local_resources, loaded.timeout_seconds,
                    lambda: self._stop_requested() or self._cancel_requested(running.work_item_id),
                )
        except Exception as exc:
            outcome = MaaExecutionOutcome(False, 'lineup_inference_failed',
                                          {'detail': type(exc).__name__})
        self._queue_result(running, loaded, outcome)
        return ExecutionCycleResult('result_queued', running.work_item_id, outcome.error_code)

    def _active(self) -> WorkItemRecord | None:
        with self._uow_factory() as uow:
            return uow.work_items.get_active(include_result_pending=False)

    def _cancel_requested(self, work_item_id: UUID) -> bool:
        with self._uow_factory() as uow:
            work = uow.work_items.get(work_item_id)
        return work is not None and work.cancel_outcome is not None

    def _record_quick_event(
        self, work: WorkItemRecord, kind: str, step_number: int | None
    ) -> None:
        code = rule_event_code(kind, step_number)
        failure_skip = kind.startswith("failure_skipped:")
        if failure_skip:
            from al1s_terminal.execution.failure_skip import REASONS

            parts = kind.split(":")
            if (
                len(parts) != 3 or parts[1] not in {"recognition", "execution"}
                or parts[2] not in REASONS
                or type(step_number) is not int or not 1 <= step_number <= 1000
            ):
                raise ValueError("Invalid failure skip reason")
            code = "maa_failure_skipped:" + ":".join(parts[1:]) + f":{step_number}"
        with self._uow_factory() as uow:
            uow.quick_test_events.append(
                work, kind="log" if code else kind,
                step_number=None if code else step_number,
                code=code, now=self._clock()
            )
        self._quick_event_failures = 0

    def _warn_quick_event_failure(self, exc: Exception) -> None:
        self._quick_event_failures += 1
        now = self._clock()
        if (
            self._quick_event_last_warning is not None
            and now - self._quick_event_last_warning < timedelta(minutes=1)
        ):
            return
        self._quick_event_last_warning = now
        structlog.get_logger(__name__).warning(
            "quick_test_event_persist_failed",
            error_type=type(exc).__name__,
            failure_count=self._quick_event_failures,
        )

    def _safe_quick_event(
        self, work: WorkItemRecord, kind: str, step_number: int | None
    ) -> None:
        try:
            self._record_quick_event(work, kind, step_number)
        except Exception as exc:
            self._warn_quick_event_failure(exc)

    def _next_queued(self) -> WorkItemRecord | None:
        with self._uow_factory() as uow:
            return uow.work_items.get_next_queued(now=self._clock())

    def _live_serial(self, work_item: WorkItemRecord) -> str | None:
        if work_item.target_device_id is None:
            return None
        serial = self._device_discovery.serial_for_target(work_item.target_device_id)
        if serial is None:
            return None
        try:
            self._adb.require_device(serial)
        except AdbProviderError:
            return None
        return serial

    def _start(self, loaded: LoadedExecutionWork) -> WorkItemRecord:
        work_item = loaded.work_item
        if work_item.kind is WorkItemKind.QUICK_TEST:
            with self._uow_factory() as uow:
                return uow.work_items.mark_running(
                    work_item.work_item_id,
                    lease_id=None,
                    lease_version=None,
                    now=self._clock(),
                )
        if loaded.package is None:
            raise RuntimeError("formal work is missing package context")
        identity = self._secret_store.load()
        if identity is None:
            raise RuntimeError("terminal identity is unavailable")
        report_id = uuid5(START_REPORT_NAMESPACE, str(loaded.package.attempt_id))
        started_at = self._clock()
        with self._uow_factory() as uow:
            permit = uow.offline_permits.get_by_work_item(work_item.work_item_id)
        if permit is None or permit.status is not OfflinePermitStatus.ISSUED:
            raise PlatformError(409, "offline_permit_missing", "offline permit is unavailable")
        start_payload = AttemptStartPayload(
            report_id=report_id,
            package_id=loaded.package.package_id,
            offline_permit_id=permit.permit_id,
            offline_permit_token=permit.token,
            occurred_at=started_at,
        )
        try:
            receipt = self._platform.start_attempt(
                identity.terminal_id,
                identity.credential,
                loaded.package.attempt_id,
                start_payload,
            )
        except PlatformUnavailableError:
            if not loaded.package.manifest.get("queue_order"):
                raise PlatformError(
                    409,
                    "offline_queue_order_missing",
                    "legacy package requires online start to verify FIFO order",
                ) from None
            return self._start_offline(work_item, loaded.package.attempt_id, start_payload)
        with self._uow_factory() as uow:
            uow.offline_permits.consume(
                work_item.work_item_id,
                start_report_id=report_id,
                now=started_at,
            )
            return uow.work_items.mark_running(
                work_item.work_item_id,
                lease_id=receipt.lease_id,
                lease_version=receipt.lease_version,
                now=started_at,
            )

    def _start_offline(
        self,
        work_item: WorkItemRecord,
        attempt_id: UUID,
        payload: AttemptStartPayload,
    ) -> WorkItemRecord:
        with self._uow_factory() as uow:
            uow.offline_permits.consume(
                work_item.work_item_id,
                start_report_id=payload.report_id,
                now=payload.occurred_at,
            )
            running = uow.work_items.mark_running(
                work_item.work_item_id,
                lease_id=None,
                lease_version=None,
                now=payload.occurred_at,
            )
            uow.outbox.enqueue(
                report_id=payload.report_id,
                kind=ReportKind.ATTEMPT_START,
                payload={
                    **payload.model_dump(mode="json"),
                    "attempt_id": str(attempt_id),
                },
                work_item_id=work_item.work_item_id,
                now=payload.occurred_at,
            )
            return running

    def _start_and_queue_failure(
        self,
        work_item: WorkItemRecord,
        error_code: str,
        *,
        loaded: LoadedExecutionWork | None = None,
    ) -> None:
        context = loaded
        if context is None:
            try:
                context = self._loader.failure_context(work_item)
            except Exception:
                context = None
        if context is None:
            self._reject_unloadable(work_item, error_code)
            return
        running = self._start(context)
        self._queue_result(
            running,
            context,
            MaaExecutionOutcome(False, error_code[:100], {}),
        )

    def _reject_unloadable(self, work_item: WorkItemRecord, error_code: str) -> None:
        with self._uow_factory() as uow:
            uow.work_items.transition(
                work_item.work_item_id,
                expected=WorkItemStatus.QUEUED,
                target=WorkItemStatus.REJECTED,
                now=self._clock(),
                reason_code=error_code[:100],
            )

    def _queue_interrupted(self, work_item: WorkItemRecord) -> None:
        try:
            loaded = self._loader.load(work_item)
        except Exception:
            self._reject_running(work_item, "execution_interrupted")
            return
        self._queue_result(
            work_item,
            loaded,
            MaaExecutionOutcome(False, "execution_interrupted", {}),
        )

    def _reject_running(self, work_item: WorkItemRecord, error_code: str) -> None:
        with self._uow_factory() as uow:
            uow.work_items.transition(
                work_item.work_item_id,
                expected=WorkItemStatus.RUNNING,
                target=WorkItemStatus.INTERRUPTED,
                now=self._clock(),
                reason_code=error_code,
            )

    def _queue_result(
        self,
        running: WorkItemRecord,
        loaded: LoadedExecutionWork,
        outcome: MaaExecutionOutcome,
    ) -> None:
        report_id = uuid5(RESULT_REPORT_NAMESPACE, str(running.work_item_id))
        payload = self._result_payload(report_id, running, loaded, outcome)
        kind = (
            ReportKind.ATTEMPT_RESULT
            if running.kind is WorkItemKind.FORMAL_TASK
            else ReportKind.QUICK_TEST_RESULT
        )
        with self._uow_factory() as uow:
            uow.outbox.enqueue(
                report_id=report_id,
                kind=kind,
                payload=payload,
                work_item_id=running.work_item_id,
                now=self._clock(),
            )
            uow.work_items.transition(
                running.work_item_id,
                expected=WorkItemStatus.RUNNING,
                target=WorkItemStatus.RESULT_PENDING,
                now=self._clock(),
                reason_code=outcome.error_code or "execution_succeeded",
            )

    def _result_payload(
        self,
        report_id: UUID,
        running: WorkItemRecord,
        loaded: LoadedExecutionWork,
        outcome: MaaExecutionOutcome,
    ) -> dict[str, Any]:
        occurred_at = self._clock().isoformat()
        diagnostic = final_diagnostic(outcome)
        if running.kind is WorkItemKind.FORMAL_TASK:
            if loaded.package is None:
                raise RuntimeError("formal result has no package context")
            return {
                "protocol_version": 1,
                "report_id": str(report_id),
                "attempt_id": str(loaded.package.attempt_id),
                "package_id": str(loaded.package.package_id),
                "lease_id": str(running.lease_id) if running.lease_id else None,
                "lease_version": running.lease_version,
                "result": (
                    "cancelled"
                    if outcome.error_code == "execution_cancelled"
                    else ("success" if outcome.passed else "failure")
                ),
                "error_code": outcome.error_code,
                "retryable": not outcome.passed and outcome.error_code in RETRYABLE_SCRIPT_ERRORS,
                "occurred_at": occurred_at,
                "diagnostic": diagnostic,
            }
        if loaded.script_id is None:
            raise RuntimeError("quick test has no script id")
        return {
            "report_id": str(report_id),
            "script_id": str(loaded.script_id),
            "session_id": str(running.remote_id),
            "candidate_version_id": running.payload["candidate_version_id"],
            "candidate_manifest_hash": running.payload["candidate_manifest_hash"],
            "definition_hash": running.content_hash,
            "executor_version": self._executor_version,
            "passed": outcome.passed,
            "error_code": outcome.error_code,
            "diagnostic": diagnostic,
            "occurred_at": occurred_at,
        }


def _target_package(loaded: LoadedExecutionWork) -> str | None:
    if loaded.plan is None:
        return None
    packages = {
        str(module.target.get("application_package") or "").strip()
        for module in loaded.plan.modules
    }
    packages.discard("")
    return next(iter(packages)) if len(packages) == 1 else None
