from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine

from al1s_terminal.app.cancellation import CancellationCoordinator
from al1s_terminal.app.delivery import DeliveryCoordinator
from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.transport.delivery_models import (
    CommandAcknowledgementPayload,
    CommandAcknowledgementResultPayload,
    PackageReceiptPayload,
    PackageReceiptResultPayload,
    QuickTestClaimPayload,
    QuickTestPagePayload,
    TerminalCommandPagePayload,
    TerminalCommandPayload,
)
from al1s_terminal.transport.platform import (
    PlatformCredentialRejectedError,
    PlatformError,
)
from al1s_terminal.types import OutboxStatus, ReportKind, SecureIdentity

NOW = datetime(2026, 8, 31, 18, 0, tzinfo=UTC)


class FakePackageReceiver:
    def __init__(self) -> None:
        self.received: list[TerminalCommandPayload] = []

    def receive(self, command: TerminalCommandPayload) -> None:
        self.received.append(command)


class FakeQuickTestReceiver:
    def __init__(self) -> None:
        self.received: list[QuickTestClaimPayload] = []

    def receive(self, claimed: QuickTestClaimPayload) -> None:
        self.received.append(claimed)


class FakeCancellationCoordinator(CancellationCoordinator):
    def __init__(self) -> None:
        self.received: list[TerminalCommandPayload] = []

    def acknowledge(self, command: TerminalCommandPayload) -> CommandAcknowledgementPayload:
        self.received.append(command)
        return CommandAcknowledgementPayload(
            report_id=uuid4(),
            outcome="running_cancel_accepted",
            occurred_at=NOW,
        )


class FakeDeliveryPlatform:
    def __init__(self) -> None:
        self.commands: list[TerminalCommandPayload] = []
        self.submitted: list[PackageReceiptPayload] = []
        self.acknowledged: list[tuple[UUID, CommandAcknowledgementPayload]] = []
        self.submission_error: PlatformError | None = None
        self.ack_errors: dict[UUID, PlatformError] = {}
        self.quick_tests = QuickTestPagePayload(items=[])

    def list_commands(
        self, terminal_id: UUID, credential: str, *, limit: int
    ) -> TerminalCommandPagePayload:
        assert credential == "credential"
        return TerminalCommandPagePayload(items=self.commands[:limit])

    def submit_package_receipt(
        self,
        terminal_id: UUID,
        credential: str,
        package_id: UUID,
        payload: PackageReceiptPayload,
    ) -> PackageReceiptResultPayload:
        if self.submission_error is not None:
            raise self.submission_error
        self.submitted.append(payload)
        return PackageReceiptResultPayload(
            report_id=payload.report_id,
            disposition=payload.disposition,
            package_status="accepted",
            execution_status="queued",
            rejection_code=None,
            offline_start_permit=None,
        )

    def list_quick_tests(
        self, terminal_id: UUID, credential: str, *, limit: int
    ) -> QuickTestPagePayload:
        return self.quick_tests

    def claim_quick_test(
        self, terminal_id: UUID, credential: str, session_id: UUID
    ) -> QuickTestClaimPayload:
        raise AssertionError("no quick tests were configured")

    def acknowledge_command(
        self,
        terminal_id: UUID,
        credential: str,
        command_id: UUID,
        payload: CommandAcknowledgementPayload,
    ) -> CommandAcknowledgementResultPayload:
        assert credential == "credential"
        if command_id in self.ack_errors:
            raise self.ack_errors[command_id]
        self.acknowledged.append((command_id, payload))
        return CommandAcknowledgementResultPayload(
            report_id=payload.report_id,
            disposition="accepted",
            command_status="acknowledged",
            execution_status="running",
        )


def _coordinator(
    local_engine: Engine,
    tmp_path: Path,
    platform: FakeDeliveryPlatform,
    receiver: FakePackageReceiver,
    quick_test_receiver: FakeQuickTestReceiver | None = None,
    cancellation_coordinator: CancellationCoordinator | None = None,
) -> DeliveryCoordinator:
    secret_store = FileSecretStore(tmp_path / "secrets" / "terminal-credential")
    secret_store.save(SecureIdentity(uuid4(), uuid4(), "credential", 1, 1))
    return DeliveryCoordinator(
        platform=platform,  # type: ignore[arg-type]
        secret_store=secret_store,
        package_receiver=receiver,  # type: ignore[arg-type]
        quick_test_receiver=quick_test_receiver or FakeQuickTestReceiver(),  # type: ignore[arg-type]
        cancellation_coordinator=cancellation_coordinator,
        uow_factory=lambda: LocalUnitOfWork.from_engine(local_engine),
        clock=lambda: NOW,
    )


def _enqueue_receipt(local_engine: Engine) -> UUID:
    report_id = uuid4()
    package_id = uuid4()
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        uow.outbox.enqueue(
            report_id=report_id,
            kind=ReportKind.PACKAGE_RECEIPT,
            payload={
                "protocol_version": 1,
                "report_id": str(report_id),
                "command_id": str(uuid4()),
                "attempt_id": str(uuid4()),
                "disposition": "accepted",
                "rejection_code": None,
                "diagnostic": None,
                "occurred_at": NOW.isoformat(),
                "package_id": str(package_id),
            },
            now=NOW,
        )
    return report_id


def test_reconcile_dispatches_available_package_commands(
    local_engine: Engine, tmp_path: Path
) -> None:
    platform = FakeDeliveryPlatform()
    receiver = FakePackageReceiver()
    platform.commands.append(
        TerminalCommandPayload(
            command_id=uuid4(),
            kind="task_package_available",
            package_id=uuid4(),
            attempt_id=uuid4(),
            delivery_no=1,
            status="pending",
            payload={"package_hash": "0" * 64},
            available_at=NOW,
            created_at=NOW,
            row_version=1,
        )
    )

    result = _coordinator(local_engine, tmp_path, platform, receiver).reconcile()

    assert receiver.received == platform.commands
    assert result == {
        "commands_seen": 1,
        "packages_received": 1,
        "reports_sent": 0,
        "quick_tests_seen": 0,
        "quick_tests_received": 0,
        "cancellations_acknowledged": 0,
    }


def test_reconcile_acknowledges_cancellation_commands(local_engine: Engine, tmp_path: Path) -> None:
    platform = FakeDeliveryPlatform()
    receiver = FakePackageReceiver()
    cancellation = FakeCancellationCoordinator()
    command = TerminalCommandPayload(
        command_id=uuid4(),
        kind="cancel_requested",
        package_id=uuid4(),
        attempt_id=uuid4(),
        delivery_no=1,
        status="pending",
        payload={"reason": "operator", "cleanup_grace_seconds": 60},
        available_at=NOW,
        created_at=NOW,
        row_version=1,
    )
    platform.commands.append(command)

    result = _coordinator(
        local_engine,
        tmp_path,
        platform,
        receiver,
        cancellation_coordinator=cancellation,
    ).reconcile()

    assert cancellation.received == [command]
    assert len(platform.acknowledged) == 1
    assert platform.acknowledged[0][0] == command.command_id
    assert platform.acknowledged[0][1].outcome == "running_cancel_accepted"
    assert result["cancellations_acknowledged"] == 1
    assert receiver.received == []


def test_transient_receipt_failure_returns_report_to_pending(
    local_engine: Engine, tmp_path: Path
) -> None:
    platform = FakeDeliveryPlatform()
    platform.submission_error = PlatformError(503, "unavailable", "try again")
    receiver = FakePackageReceiver()
    report_id = _enqueue_receipt(local_engine)

    sent = _coordinator(local_engine, tmp_path, platform, receiver).flush_outbox()

    assert sent == 0
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        report = uow.outbox.get(report_id)
        assert report is not None
        assert report.status is OutboxStatus.PENDING
        assert report.attempt_count == 1
        assert report.last_error_code == "unavailable"


@pytest.mark.parametrize("rejected", [False, True])
def test_cancel_start_reconciliation_conflict_does_not_starve_later_commands(
    local_engine: Engine, tmp_path: Path, rejected: bool
) -> None:
    platform = FakeDeliveryPlatform()
    cancellation = FakeCancellationCoordinator()
    platform.commands = [
        TerminalCommandPayload(
            command_id=uuid4(),
            kind="cancel_requested",
            package_id=uuid4(),
            attempt_id=uuid4(),
            delivery_no=1,
            status="pending",
            payload={},
            available_at=NOW,
            created_at=NOW,
            row_version=1,
        )
        for _ in range(2)
    ]
    error = (
        PlatformCredentialRejectedError(401, "invalid_terminal_credential", "revoked")
        if rejected
        else PlatformError(409, "cancel_outcome_mismatch", "start pending")
    )
    platform.ack_errors[platform.commands[0].command_id] = error
    coordinator = _coordinator(
        local_engine,
        tmp_path,
        platform,
        FakePackageReceiver(),
        cancellation_coordinator=cancellation,
    )
    if rejected:
        with pytest.raises(PlatformCredentialRejectedError):
            coordinator.reconcile()
        assert len(cancellation.received) == 1
        return
    result = coordinator.reconcile()
    assert cancellation.received == platform.commands
    assert result["cancellations_acknowledged"] == 1
    assert platform.acknowledged[0][0] == platform.commands[1].command_id


def test_credential_rejection_is_not_hidden_as_a_retry(
    local_engine: Engine, tmp_path: Path
) -> None:
    platform = FakeDeliveryPlatform()
    platform.submission_error = PlatformCredentialRejectedError(
        401, "invalid_terminal_credential", "revoked"
    )
    receiver = FakePackageReceiver()
    report_id = _enqueue_receipt(local_engine)
    coordinator = _coordinator(local_engine, tmp_path, platform, receiver)

    with pytest.raises(PlatformCredentialRejectedError):
        coordinator.flush_outbox()

    with LocalUnitOfWork.from_engine(local_engine) as uow:
        report = uow.outbox.get(report_id)
        assert report is not None
        assert report.status is OutboxStatus.CLAIMED
