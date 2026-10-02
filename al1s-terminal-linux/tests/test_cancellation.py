from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Engine

from al1s_terminal.app.cancellation import CancellationCoordinator
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.transport.delivery_models import TerminalCommandPayload
from al1s_terminal.types import (
    CancellationOutcome,
    OfflinePermitStatus,
    OfflineStartPermitRecord,
    WorkItemKind,
    WorkItemRecord,
    WorkItemStatus,
)

NOW = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def _formal_work(engine: Engine) -> tuple[WorkItemRecord, OfflineStartPermitRecord, UUID]:
    package_id = uuid4()
    attempt_id = uuid4()
    with LocalUnitOfWork.from_engine(engine) as uow:
        work = uow.work_items.add(
            kind=WorkItemKind.FORMAL_TASK,
            remote_id=package_id,
            content_hash="1" * 64,
            target_device_id=uuid4(),
            available_at=NOW,
            payload={},
            now=NOW,
        )
        uow.packages.add_receiving(
            package_id=package_id,
            work_item_id=work.work_item_id,
            attempt_id=attempt_id,
            execution_id=uuid4(),
            package_hash="1" * 64,
            protocol_version=1,
            package_schema_version=1,
            snapshot_schema_version=1,
            manifest={},
            now=NOW,
        )
        uow.packages.mark_ready(package_id, relative_path="packages/test.json", now=NOW)
        work = uow.work_items.transition(
            work.work_item_id,
            expected=WorkItemStatus.RECEIVING,
            target=WorkItemStatus.QUEUED,
            now=NOW,
        )
        permit = uow.offline_permits.save_issued(
            permit_id=uuid4(),
            work_item_id=work.work_item_id,
            attempt_id=attempt_id,
            package_id=package_id,
            package_hash="1" * 64,
            permit_version=1,
            token="test-permit",
            issued_at=NOW,
            expires_at=NOW + timedelta(hours=1),
        )
    return work, permit, attempt_id


def _command(package_id: UUID, attempt_id: UUID) -> TerminalCommandPayload:
    return TerminalCommandPayload(
        command_id=uuid4(),
        kind="cancel_requested",
        package_id=package_id,
        attempt_id=attempt_id,
        delivery_no=1,
        status="pending",
        payload={"reason": "operator", "cleanup_grace_seconds": 60},
        available_at=NOW,
        created_at=NOW,
        row_version=1,
    )


def test_cancel_before_start_revokes_permit_without_running_phone_work(
    local_engine: Engine,
) -> None:
    work, permit, attempt_id = _formal_work(local_engine)
    command = _command(work.remote_id, attempt_id)
    coordinator = CancellationCoordinator(
        uow_factory=lambda: LocalUnitOfWork.from_engine(local_engine),
        clock=lambda: NOW,
    )

    first = coordinator.acknowledge(command)
    replay = coordinator.acknowledge(command)

    assert first == replay
    assert first.outcome == CancellationOutcome.CANCELLED_BEFORE_START.value
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        updated = uow.work_items.get(work.work_item_id)
        revoked = uow.offline_permits.get_by_work_item(work.work_item_id)
        assert updated is not None and updated.status is WorkItemStatus.CANCELLED
        assert updated.cancel_command_id == command.command_id
        assert revoked is not None and revoked.permit_id == permit.permit_id
        assert revoked.status is OfflinePermitStatus.REVOKED


def test_cancel_acknowledgement_distinguishes_running_from_completed(
    local_engine: Engine,
) -> None:
    running_work, _permit, running_attempt = _formal_work(local_engine)
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        uow.work_items.mark_running(
            running_work.work_item_id,
            lease_id=None,
            lease_version=None,
            now=NOW,
        )
    coordinator = CancellationCoordinator(
        uow_factory=lambda: LocalUnitOfWork.from_engine(local_engine),
        clock=lambda: NOW,
    )

    running = coordinator.acknowledge(_command(running_work.remote_id, running_attempt))

    assert running.outcome == CancellationOutcome.RUNNING_CANCEL_ACCEPTED.value

    completed_work, _permit, completed_attempt = _formal_work(local_engine)
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        uow.work_items.mark_running(
            completed_work.work_item_id,
            lease_id=None,
            lease_version=None,
            now=NOW,
        )
        uow.work_items.transition(
            completed_work.work_item_id,
            expected=WorkItemStatus.RUNNING,
            target=WorkItemStatus.RESULT_PENDING,
            now=NOW,
        )

    completed = coordinator.acknowledge(_command(completed_work.remote_id, completed_attempt))

    assert completed.outcome == CancellationOutcome.ALREADY_COMPLETED.value
