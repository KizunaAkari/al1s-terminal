"""Durable ordered quick-test events, independent of formal result outbox."""

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from al1s_terminal.persistence.models import QuickTestEventRow
from al1s_terminal.types import QuickTestEventRecord, WorkItemRecord


class QuickTestEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self, work: WorkItemRecord, *, kind: str, step_number: int | None,
        now: datetime, code: str | None = None,
    ) -> QuickTestEventRecord | None:
        last = self._session.scalar(
            select(func.max(QuickTestEventRow.sequence)).where(
                QuickTestEventRow.session_id == str(work.remote_id)
            )
        ) or 0
        if last >= 1000:
            return None
        sequence = last + 1
        code = "events_truncated" if sequence == 1000 else code
        row = QuickTestEventRow(
            session_id=str(work.remote_id), sequence=sequence,
            work_item_id=str(work.work_item_id),
            kind="log" if code else kind,
            step_number=None if code else step_number,
            code=code, created_at=now, confirmed_at=None,
        )
        self._session.add(row)
        self._session.flush()
        return _record(row)

    def pending_batch(self, *, limit: int = 50) -> tuple[QuickTestEventRecord, ...]:
        if not 1 <= limit <= 50:
            raise ValueError("quick-test event limit must be 1 to 50")
        first = self._session.scalar(
            select(QuickTestEventRow)
            .where(QuickTestEventRow.confirmed_at.is_(None))
            .order_by(QuickTestEventRow.created_at,
                      QuickTestEventRow.session_id, QuickTestEventRow.sequence)
            .limit(1)
        )
        if first is None:
            return ()
        rows = self._session.scalars(
            select(QuickTestEventRow).where(
                QuickTestEventRow.session_id == first.session_id,
                QuickTestEventRow.confirmed_at.is_(None),
            ).order_by(QuickTestEventRow.sequence).limit(limit)
        ).all()
        return tuple(_record(row) for row in rows)

    def confirm_batch(self, session_id: UUID, last_sequence: int, now: datetime) -> int:
        result = self._session.execute(
            update(QuickTestEventRow)
            .where(QuickTestEventRow.session_id == str(session_id),
                   QuickTestEventRow.sequence <= last_sequence,
                   QuickTestEventRow.confirmed_at.is_(None))
            .values(confirmed_at=now)
        )
        return cast(CursorResult[Any], result).rowcount or 0

    def discard_session(self, session_id: UUID) -> int:
        """Remove events that the platform can no longer accept for this session."""
        rows = self._session.scalars(
            select(QuickTestEventRow).where(
                QuickTestEventRow.session_id == str(session_id),
                QuickTestEventRow.confirmed_at.is_(None),
            )
        ).all()
        for row in rows:
            self._session.delete(row)
        return len(rows)

    def delete_expired(self, *, before: datetime, limit: int = 50) -> int:
        # A partial session purge would leave a sequence gap that the platform
        # cannot accept. At most five sessions (each capped at 1000 events) go
        # in one GC transaction.
        session_ids = self._session.scalars(
            select(QuickTestEventRow.session_id).where(
                QuickTestEventRow.created_at <= before,
            ).distinct().order_by(QuickTestEventRow.session_id).limit(min(limit, 5))
        ).all()
        if not session_ids:
            return 0
        result = self._session.execute(
            delete(QuickTestEventRow).where(QuickTestEventRow.session_id.in_(session_ids))
        )
        return cast(CursorResult[Any], result).rowcount or 0


def _record(row: QuickTestEventRow) -> QuickTestEventRecord:
    # SQLite drops the timezone from the UTC value stored by the event writer.
    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return QuickTestEventRecord(
        session_id=UUID(row.session_id), sequence=row.sequence,
        work_item_id=UUID(row.work_item_id), kind=row.kind,
        step_number=row.step_number, code=row.code, created_at=created_at,
    )
