from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Select, and_, case, or_, select, update
from sqlalchemy.orm import Session

from al1s_terminal.persistence.models import (
    OutboxReportRow,
)
from al1s_terminal.persistence.report_ordering import execution_report_head
from al1s_terminal.persistence.repository_records import (
    _outbox,
)
from al1s_terminal.types import (
    OutboxReportRecord,
    OutboxStatus,
    ReportKind,
)


class OutboxRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, report_id: UUID) -> OutboxReportRecord | None:
        row = self._session.get(OutboxReportRow, str(report_id))
        return None if row is None else _outbox(row)

    def enqueue(
        self,
        *,
        report_id: UUID,
        kind: ReportKind,
        payload: dict[str, Any],
        now: datetime,
        work_item_id: UUID | None = None,
    ) -> OutboxReportRecord:
        existing = self._session.get(OutboxReportRow, str(report_id))
        if existing is not None:
            if existing.kind != kind.value or existing.payload != payload:
                raise ValueError("report id was reused with different content")
            return _outbox(existing)
        row = OutboxReportRow(
            report_id=str(report_id),
            kind=kind.value,
            work_item_id=str(work_item_id) if work_item_id else None,
            payload=payload,
            status=OutboxStatus.PENDING.value,
            attempt_count=0,
            available_at=now,
            claimed_by=None,
            claimed_until=None,
            confirmed_at=None,
            last_error_code=None,
            created_at=now,
            row_version=1,
        )
        self._session.add(row)
        self._session.flush()
        return _outbox(row)

    def claim_batch(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[OutboxReportRecord, ...]:
        if not 1 <= limit <= 50:
            raise ValueError("outbox limit must be between 1 and 50")
        # A later start must not pass an earlier unconfirmed result, even when
        # the earlier report is backing off or claimed by another dispatcher.
        execution_head = execution_report_head(self._session)
        execution_kinds = (ReportKind.ATTEMPT_START.value, ReportKind.ATTEMPT_RESULT.value)
        statement: Select[tuple[OutboxReportRow]] = (
            select(OutboxReportRow)
            .where(
                OutboxReportRow.available_at <= now,
                or_(
                    OutboxReportRow.kind.not_in(execution_kinds),
                    OutboxReportRow.report_id == execution_head,
                ),
                or_(
                    OutboxReportRow.status == OutboxStatus.PENDING.value,
                    and_(
                        OutboxReportRow.status == OutboxStatus.CLAIMED.value,
                        OutboxReportRow.claimed_until <= now,
                    ),
                ),
            )
            .order_by(
                OutboxReportRow.available_at,
                OutboxReportRow.created_at,
                case(
                    (OutboxReportRow.kind == ReportKind.PACKAGE_RECEIPT.value, 0),
                    (OutboxReportRow.kind == ReportKind.ATTEMPT_START.value, 1),
                    (OutboxReportRow.kind == ReportKind.ATTEMPT_RESULT.value, 2),
                    else_=3,
                ),
                OutboxReportRow.report_id,
            )
            .limit(limit)
        )
        rows = tuple(self._session.scalars(statement))
        claimed_until = now + lease_duration
        for row in rows:
            row.status = OutboxStatus.CLAIMED.value
            row.claimed_by = worker_id
            row.claimed_until = claimed_until
            row.attempt_count += 1
            row.row_version += 1
        self._session.flush()
        return tuple(_outbox(row) for row in rows)

    def confirm(self, report_id: UUID, *, worker_id: str, now: datetime) -> bool:
        result = self._session.execute(
            update(OutboxReportRow)
            .where(
                OutboxReportRow.report_id == str(report_id),
                OutboxReportRow.status == OutboxStatus.CLAIMED.value,
                OutboxReportRow.claimed_by == worker_id,
            )
            .values(
                status=OutboxStatus.CONFIRMED.value,
                confirmed_at=now,
                claimed_by=None,
                claimed_until=None,
                row_version=OutboxReportRow.row_version + 1,
            )
            .returning(OutboxReportRow.report_id)
        )
        return result.scalar_one_or_none() is not None

    def release_failed(
        self,
        report_id: UUID,
        *,
        worker_id: str,
        available_at: datetime,
        error_code: str,
    ) -> bool:
        result = self._session.execute(
            update(OutboxReportRow)
            .where(
                OutboxReportRow.report_id == str(report_id),
                OutboxReportRow.status == OutboxStatus.CLAIMED.value,
                OutboxReportRow.claimed_by == worker_id,
            )
            .values(
                status=OutboxStatus.PENDING.value,
                available_at=available_at,
                claimed_by=None,
                claimed_until=None,
                last_error_code=error_code[:100],
                row_version=OutboxReportRow.row_version + 1,
            )
            .returning(OutboxReportRow.report_id)
        )
        return result.scalar_one_or_none() is not None
