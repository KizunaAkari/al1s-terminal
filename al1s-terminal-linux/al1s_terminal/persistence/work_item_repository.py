from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from al1s_terminal.persistence.models import (
    InboxWorkItemRow,
    LocalTransitionRow,
)
from al1s_terminal.persistence.repository_records import (
    _work_item,
)
from al1s_terminal.types import (
    CancellationOutcome,
    WorkItemKind,
    WorkItemRecord,
    WorkItemStatus,
)


class WorkItemRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_remote(self, kind: WorkItemKind, remote_id: UUID) -> WorkItemRecord | None:
        row = self._session.scalar(
            select(InboxWorkItemRow).where(
                InboxWorkItemRow.kind == kind.value,
                InboxWorkItemRow.remote_id == str(remote_id),
            )
        )
        return None if row is None else _work_item(row)

    def get(self, work_item_id: UUID) -> WorkItemRecord | None:
        row = self._session.get(InboxWorkItemRow, str(work_item_id))
        return None if row is None else _work_item(row)

    def get_active(self, *, include_result_pending: bool = True) -> WorkItemRecord | None:
        statuses = [WorkItemStatus.RUNNING.value]
        if include_result_pending:
            statuses.append(WorkItemStatus.RESULT_PENDING.value)
        row = self._session.scalar(
            select(InboxWorkItemRow)
            .where(InboxWorkItemRow.status.in_(statuses))
            .order_by(InboxWorkItemRow.updated_at, InboxWorkItemRow.id)
            .limit(1)
        )
        return None if row is None else _work_item(row)

    def get_next_queued(self, *, now: datetime) -> WorkItemRecord | None:
        row = self._session.scalar(
            select(InboxWorkItemRow)
            .where(
                InboxWorkItemRow.status == WorkItemStatus.QUEUED.value,
                InboxWorkItemRow.available_at <= now,
            )
            .order_by(
                InboxWorkItemRow.available_at,
                InboxWorkItemRow.queue_enqueued_at,
                InboxWorkItemRow.queue_order_id,
            )
            .limit(1)
        )
        return None if row is None else _work_item(row)

    def add(
        self,
        *,
        kind: WorkItemKind,
        remote_id: UUID,
        content_hash: str,
        target_device_id: UUID | None,
        available_at: datetime,
        payload: dict[str, Any],
        now: datetime,
        queue_enqueued_at: datetime | None = None,
        queue_order_id: UUID | None = None,
    ) -> WorkItemRecord:
        existing = self.get_by_remote(kind, remote_id)
        if existing is not None:
            if existing.content_hash != content_hash:
                raise ValueError("remote work item id was reused with different content")
            return existing
        local_id = uuid4()
        row = InboxWorkItemRow(
            id=str(local_id),
            kind=kind.value,
            remote_id=str(remote_id),
            content_hash=content_hash,
            target_device_id=str(target_device_id) if target_device_id else None,
            status=WorkItemStatus.RECEIVING.value,
            available_at=available_at,
            queue_enqueued_at=queue_enqueued_at or now,
            queue_order_id=str(queue_order_id or local_id),
            payload=payload,
            lease_id=None,
            lease_version=None,
            cancel_command_id=None,
            cancel_requested_at=None,
            cancel_reason=None,
            cancel_outcome=None,
            created_at=now,
            updated_at=now,
            row_version=1,
        )
        self._session.add(row)
        self._session.add(
            LocalTransitionRow(
                id=str(uuid4()),
                work_item_id=row.id,
                from_status=None,
                to_status=WorkItemStatus.RECEIVING.value,
                reason_code="received",
                created_at=now,
            )
        )
        self._session.flush()
        return _work_item(row)

    def transition(
        self,
        work_item_id: UUID,
        *,
        expected: WorkItemStatus,
        target: WorkItemStatus,
        now: datetime,
        reason_code: str | None = None,
    ) -> WorkItemRecord:
        row = self._session.get(InboxWorkItemRow, str(work_item_id))
        if row is None:
            raise KeyError(work_item_id)
        if row.status != expected.value:
            raise ValueError(f"expected {expected.value}, found {row.status}")
        row.status = target.value
        row.updated_at = now
        row.row_version += 1
        self._session.add(
            LocalTransitionRow(
                id=str(uuid4()),
                work_item_id=row.id,
                from_status=expected.value,
                to_status=target.value,
                reason_code=reason_code,
                created_at=now,
            )
        )
        self._session.flush()
        return _work_item(row)

    def mark_running(
        self,
        work_item_id: UUID,
        *,
        lease_id: UUID | None,
        lease_version: int | None,
        now: datetime,
    ) -> WorkItemRecord:
        row = self._session.get(InboxWorkItemRow, str(work_item_id))
        if row is None:
            raise KeyError(work_item_id)
        if row.status != WorkItemStatus.QUEUED.value:
            raise ValueError(f"expected queued, found {row.status}")
        if (lease_id is None) != (lease_version is None):
            raise ValueError("lease id and version must be provided together")
        row.status = WorkItemStatus.RUNNING.value
        row.lease_id = str(lease_id) if lease_id else None
        row.lease_version = lease_version
        row.updated_at = now
        row.row_version += 1
        self._session.add(
            LocalTransitionRow(
                id=str(uuid4()),
                work_item_id=row.id,
                from_status=WorkItemStatus.QUEUED.value,
                to_status=WorkItemStatus.RUNNING.value,
                reason_code="execution_started",
                created_at=now,
            )
        )
        self._session.flush()
        return _work_item(row)

    def attach_lease(
        self,
        work_item_id: UUID,
        *,
        lease_id: UUID,
        lease_version: int,
        now: datetime,
    ) -> WorkItemRecord:
        row = self._session.get(InboxWorkItemRow, str(work_item_id))
        if row is None:
            raise KeyError(work_item_id)
        if row.status not in {
            WorkItemStatus.RUNNING.value,
            WorkItemStatus.RESULT_PENDING.value,
        }:
            raise ValueError(f"cannot attach lease to {row.status}")
        if row.lease_id is not None:
            if row.lease_id != str(lease_id) or row.lease_version != lease_version:
                raise ValueError("platform lease cannot change")
            return _work_item(row)
        row.lease_id = str(lease_id)
        row.lease_version = lease_version
        row.updated_at = now
        row.row_version += 1
        self._session.flush()
        return _work_item(row)

    def record_cancellation(
        self,
        work_item_id: UUID,
        *,
        command_id: UUID,
        reason: str,
        now: datetime,
    ) -> WorkItemRecord:
        row = self._session.get(InboxWorkItemRow, str(work_item_id))
        if row is None:
            raise KeyError(work_item_id)
        if row.cancel_command_id is not None:
            if row.cancel_command_id != str(command_id):
                raise ValueError("work item already has a different cancellation command")
            return _work_item(row)
        if row.status == WorkItemStatus.RUNNING.value:
            outcome = CancellationOutcome.RUNNING_CANCEL_ACCEPTED
            target_status = row.status
        elif row.status in {
            WorkItemStatus.RESULT_PENDING.value,
            WorkItemStatus.COMPLETED.value,
        }:
            outcome = CancellationOutcome.ALREADY_COMPLETED
            target_status = row.status
        elif row.status == WorkItemStatus.QUEUED.value:
            outcome = CancellationOutcome.CANCELLED_BEFORE_START
            target_status = WorkItemStatus.CANCELLED.value
        elif row.status == WorkItemStatus.CANCELLED.value:
            outcome = CancellationOutcome.CANCELLED_BEFORE_START
            target_status = row.status
        else:
            raise ValueError(f"cannot cancel work item in {row.status}")
        previous_status = row.status
        row.status = target_status
        row.cancel_command_id = str(command_id)
        row.cancel_requested_at = now
        row.cancel_reason = reason[:32]
        row.cancel_outcome = outcome.value
        row.updated_at = now
        row.row_version += 1
        if previous_status != target_status:
            self._session.add(
                LocalTransitionRow(
                    id=str(uuid4()),
                    work_item_id=row.id,
                    from_status=previous_status,
                    to_status=target_status,
                    reason_code="cancelled_before_start",
                    created_at=now,
                )
            )
        self._session.flush()
        return _work_item(row)
