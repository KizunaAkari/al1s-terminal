from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from pydantic import ValidationError

from al1s_terminal.app.cancellation import CancellationCoordinator
from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.execution.content_store import ContentStoreError
from al1s_terminal.execution.package_receiver import (
    PackageReceiver,
    PackageValidationError,
)
from al1s_terminal.execution.quick_test_receiver import QuickTestReceiver
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.transport.delivery import DeliveryPlatformPort
from al1s_terminal.transport.delivery_models import (
    AttemptResultPayload,
    AttemptResultReceiptPayload,
    AttemptStartPayload,
    AttemptStartResultPayload,
    PackageReceiptPayload,
    PackageReceiptResultPayload,
    QuickTestEventBatchPayload,
    QuickTestEventPayload,
    QuickTestResultPayload,
)
from al1s_terminal.transport.platform import (
    PlatformCredentialRejectedError,
    PlatformError,
)
from al1s_terminal.types import OutboxReportRecord, ReportKind, WorkItemKind, WorkItemStatus

QUICK_TEST_STOP_NAMESPACE = UUID("cbb30954-9e81-4b9f-afb1-f05b7a833fe8")
_LOG = logging.getLogger(__name__)
_CLOSED_QUICK_TEST_CODES = frozenset({
    "quick_test_session_not_found",
    "quick_test_session_conflict",
    "quick_test_event_window_closed",
})


class DeliveryCoordinator:
    def __init__(
        self,
        *,
        platform: DeliveryPlatformPort,
        secret_store: FileSecretStore,
        package_receiver: PackageReceiver,
        quick_test_receiver: QuickTestReceiver,
        cancellation_coordinator: CancellationCoordinator | None = None,
        uow_factory: Callable[[], LocalUnitOfWork],
        command_batch_size: int = 50,
        outbox_batch_size: int = 50,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._platform = platform
        self._secret_store = secret_store
        self._package_receiver = package_receiver
        self._quick_test_receiver = quick_test_receiver
        self._cancellation = cancellation_coordinator
        self._uow_factory = uow_factory
        self._command_batch_size = command_batch_size
        self._outbox_batch_size = outbox_batch_size
        self._clock = clock or (lambda: datetime.now(UTC))

    def poll_cancellations(self) -> int:
        """Control-only polling remains responsive while resource downloads run."""
        identity = self._secret_store.load()
        if identity is None or self._cancellation is None:
            return 0
        commands = self._platform.list_commands(
            identity.terminal_id,
            identity.credential,
            limit=self._command_batch_size,
        ).items
        count = 0
        for command in commands:
            if command.kind != "cancel_requested":
                continue
            with self._uow_factory() as uow:
                if command.package_id is None or uow.packages.get(command.package_id) is None:
                    continue  # The download worker has not persisted this package yet.
            try:
                receipt = self._cancellation.acknowledge(command)
                self._platform.acknowledge_command(
                    identity.terminal_id,
                    identity.credential,
                    command.command_id,
                    receipt,
                )
            except PlatformCredentialRejectedError:
                raise
            except PlatformError as exc:
                if exc.status_code == 409 and exc.code == "cancel_outcome_mismatch":
                    continue
                raise
            count += 1
        return count

    def poll_quick_test_stop(self) -> bool:
        identity = self._secret_store.load()
        if identity is None:
            return False
        with self._uow_factory() as uow:
            work = uow.work_items.get_active(include_result_pending=False)
        if work is None or work.kind is not WorkItemKind.QUICK_TEST:
            return False
        control = self._platform.get_quick_test_control(
            identity.terminal_id, identity.credential, work.remote_id
        )
        if not control.cancel_requested:
            return False
        with self._uow_factory() as uow:
            uow.work_items.record_cancellation(
                work.work_item_id,
                command_id=uuid5(QUICK_TEST_STOP_NAMESPACE, str(work.remote_id)),
                reason="quick_test_stop",
                now=self._clock(),
            )
        return True

    def flush_quick_test_events(self) -> int:
        identity = self._secret_store.load()
        if identity is None:
            return 0
        with self._uow_factory() as uow:
            events = uow.quick_test_events.pending_batch(limit=50)
        if not events:
            return 0
        payload = QuickTestEventBatchPayload(items=[QuickTestEventPayload(
            sequence=event.sequence, kind=event.kind, step_number=event.step_number,
            code=event.code, created_at=event.created_at,
        ) for event in events])
        try:
            receipt = self._platform.report_quick_test_events(
                identity.terminal_id, identity.credential, events[0].session_id, payload
            )
        except PlatformError as exc:
            if exc.code not in _CLOSED_QUICK_TEST_CODES or exc.status_code not in (404, 409):
                raise
            _LOG.warning(
                "discarding quick-test events for closed session %s: %s",
                events[0].session_id, exc.code,
            )
            with self._uow_factory() as uow:
                uow.quick_test_events.discard_session(events[0].session_id)
            return 0
        if receipt.last_sequence < events[-1].sequence:
            raise ValueError("quick-test event receipt did not confirm batch")
        with self._uow_factory() as uow:
            return uow.quick_test_events.confirm_batch(
                events[0].session_id, events[-1].sequence, self._clock()
            )

    def reconcile(self) -> dict[str, int]:
        identity = self._secret_store.load()
        if identity is None:
            return {
                "commands_seen": 0,
                "packages_received": 0,
                "reports_sent": 0,
                "quick_tests_seen": 0,
                "quick_tests_received": 0,
                "cancellations_acknowledged": 0,
            }
        sent_before = self.flush_outbox()
        commands = self._platform.list_commands(
            identity.terminal_id,
            identity.credential,
            limit=self._command_batch_size,
        ).items
        received = 0
        cancellations_acknowledged = 0
        for command in commands:
            if command.kind == "cancel_requested" and self._cancellation is not None:
                acknowledgement = self._cancellation.acknowledge(command)
                try:
                    self._platform.acknowledge_command(
                        identity.terminal_id,
                        identity.credential,
                        command.command_id,
                        acknowledgement,
                    )
                except PlatformError as exc:
                    # Start reconciliation can lag a locally completed offline task.
                    # Keep this command pending, but do not starve later cancellations.
                    if exc.status_code == 409 and exc.code == "cancel_outcome_mismatch":
                        continue
                    raise
                cancellations_acknowledged += 1
                continue
            if command.kind != "task_package_available":
                continue
            try:
                self._package_receiver.receive(command)
                received += 1
            except PackageValidationError as exc:
                self._package_receiver.queue_rejection(
                    command,
                    rejection_code=exc.code,
                    diagnostic=str(exc),
                )
            except ContentStoreError as exc:
                self._package_receiver.queue_rejection(
                    command,
                    rejection_code="RESOURCE_HASH_MISMATCH",
                    diagnostic=str(exc),
                )
        sent_after = self.flush_outbox()
        quick_tests = self._platform.list_quick_tests(
            identity.terminal_id,
            identity.credential,
            limit=self._command_batch_size,
        ).items
        quick_tests_received = 0
        for quick_test in quick_tests:
            claimed = self._platform.claim_quick_test(
                identity.terminal_id,
                identity.credential,
                quick_test.session_id,
            )
            self._quick_test_receiver.receive(claimed)
            quick_tests_received += 1
        return {
            "commands_seen": len(commands),
            "packages_received": received,
            "reports_sent": sent_before + sent_after,
            "quick_tests_seen": len(quick_tests),
            "quick_tests_received": quick_tests_received,
            "cancellations_acknowledged": cancellations_acknowledged,
        }

    def flush_outbox(self) -> int:
        identity = self._secret_store.load()
        if identity is None:
            return 0
        now = self._clock()
        worker_id = f"terminal-{identity.installation_id}"
        with self._uow_factory() as uow:
            reports = uow.outbox.claim_batch(
                worker_id=worker_id,
                now=now,
                lease_duration=timedelta(seconds=30),
                limit=self._outbox_batch_size,
            )
        confirmed = 0
        for report in reports:
            try:
                response = self._send_report(
                    identity.terminal_id,
                    identity.credential,
                    report,
                )
            except PlatformCredentialRejectedError:
                raise
            except (PlatformError, ValidationError, ValueError) as exc:
                with self._uow_factory() as uow:
                    uow.outbox.release_failed(
                        report.report_id,
                        worker_id=worker_id,
                        available_at=self._clock() + _retry_delay(report.attempt_count),
                        error_code=exc.code
                        if isinstance(exc, PlatformError)
                        else type(exc).__name__,
                    )
                continue
            with self._uow_factory() as uow:
                self._apply_report_response(uow, report, response)
                if uow.outbox.confirm(
                    report.report_id,
                    worker_id=worker_id,
                    now=self._clock(),
                ):
                    if (
                        report.kind
                        in {
                            ReportKind.ATTEMPT_RESULT,
                            ReportKind.QUICK_TEST_RESULT,
                        }
                        and report.work_item_id is not None
                    ):
                        work_item = uow.work_items.get(report.work_item_id)
                        if (
                            work_item is not None
                            and work_item.status is WorkItemStatus.RESULT_PENDING
                        ):
                            uow.work_items.transition(
                                work_item.work_item_id,
                                expected=WorkItemStatus.RESULT_PENDING,
                                target=WorkItemStatus.COMPLETED,
                                now=self._clock(),
                                reason_code="result_confirmed",
                            )
                    confirmed += 1
        return confirmed

    def _send_report(
        self,
        terminal_id: UUID,
        credential: str,
        report: OutboxReportRecord,
    ) -> (
        PackageReceiptResultPayload
        | AttemptStartResultPayload
        | AttemptResultReceiptPayload
        | object
    ):
        if report.kind is ReportKind.PACKAGE_RECEIPT:
            package_id = _payload_uuid(report.payload, "package_id")
            return self._platform.submit_package_receipt(
                terminal_id,
                credential,
                package_id,
                PackageReceiptPayload.model_validate(report.payload),
            )
        if report.kind is ReportKind.ATTEMPT_START:
            attempt_id = _payload_uuid(report.payload, "attempt_id")
            return self._platform.start_attempt(
                terminal_id,
                credential,
                attempt_id,
                AttemptStartPayload.model_validate(report.payload),
            )
        if report.kind is ReportKind.ATTEMPT_RESULT:
            attempt_id = _payload_uuid(report.payload, "attempt_id")
            payload = dict(report.payload)
            if payload.get("lease_id") is None or payload.get("lease_version") is None:
                if report.work_item_id is None:
                    raise ValueError("attempt result has no work item")
                with self._uow_factory() as uow:
                    work_item = uow.work_items.get(report.work_item_id)
                if (
                    work_item is None
                    or work_item.lease_id is None
                    or work_item.lease_version is None
                ):
                    raise ValueError("offline attempt start is not confirmed")
                payload["lease_id"] = str(work_item.lease_id)
                payload["lease_version"] = work_item.lease_version
            return self._platform.submit_attempt_result(
                terminal_id,
                credential,
                attempt_id,
                AttemptResultPayload.model_validate(payload),
            )
        if report.kind is ReportKind.QUICK_TEST_RESULT:
            script_id = _payload_uuid(report.payload, "script_id")
            report_id = _payload_uuid(report.payload, "report_id")
            return self._platform.submit_quick_test_result(
                terminal_id,
                credential,
                script_id,
                QuickTestResultPayload.model_validate(report.payload),
                idempotency_key=str(report_id),
            )
        raise ValueError(f"unsupported outbox report kind {report.kind}")

    def _apply_report_response(
        self,
        uow: LocalUnitOfWork,
        report: OutboxReportRecord,
        response: object,
    ) -> None:
        if report.kind is ReportKind.PACKAGE_RECEIPT:
            if not isinstance(response, PackageReceiptResultPayload):
                raise ValueError("package receipt response type is invalid")
            if response.disposition not in {"accepted", "duplicate"}:
                return
            if report.work_item_id is None or response.offline_start_permit is None:
                raise ValueError("accepted package receipt has no offline permit")
            package = uow.packages.get_by_work_item(report.work_item_id)
            if package is None:
                raise ValueError("accepted package manifest is missing")
            permit = response.offline_start_permit
            uow.offline_permits.save_issued(
                permit_id=permit.permit_id,
                work_item_id=report.work_item_id,
                attempt_id=package.attempt_id,
                package_id=package.package_id,
                package_hash=package.package_hash,
                permit_version=permit.permit_version,
                token=permit.token,
                issued_at=permit.issued_at,
                expires_at=permit.expires_at,
            )
            return
        if report.kind is ReportKind.ATTEMPT_START:
            if not isinstance(response, AttemptStartResultPayload):
                raise ValueError("attempt start response type is invalid")
            if report.work_item_id is None:
                raise ValueError("attempt start has no work item")
            uow.work_items.attach_lease(
                report.work_item_id,
                lease_id=response.lease_id,
                lease_version=response.lease_version,
                now=self._clock(),
            )


def _retry_delay(attempt_count: int) -> timedelta:
    seconds = min(300, 2 ** min(max(attempt_count, 1), 8))
    return timedelta(seconds=seconds)


def _payload_uuid(payload: dict[str, object], key: str) -> UUID:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"outbox report is missing {key}")
    return UUID(value)
