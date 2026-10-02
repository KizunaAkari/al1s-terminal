from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from al1s_terminal.persistence.models import (
    OfflineStartPermitRow,
)
from al1s_terminal.persistence.repository_records import (
    _as_utc,
    _offline_permit,
)
from al1s_terminal.types import (
    OfflinePermitStatus,
    OfflineStartPermitRecord,
)


class OfflineStartPermitRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_work_item(self, work_item_id: UUID) -> OfflineStartPermitRecord | None:
        row = self._session.scalar(
            select(OfflineStartPermitRow).where(
                OfflineStartPermitRow.work_item_id == str(work_item_id)
            )
        )
        return None if row is None else _offline_permit(row)

    def save_issued(
        self,
        *,
        permit_id: UUID,
        work_item_id: UUID,
        attempt_id: UUID,
        package_id: UUID,
        package_hash: str,
        permit_version: int,
        token: str,
        issued_at: datetime,
        expires_at: datetime,
    ) -> OfflineStartPermitRecord:
        existing = self.get_by_work_item(work_item_id)
        identity = (
            permit_id,
            attempt_id,
            package_id,
            package_hash,
            permit_version,
            token,
            issued_at,
            expires_at,
        )
        if existing is not None:
            current = (
                existing.permit_id,
                existing.attempt_id,
                existing.package_id,
                existing.package_hash,
                existing.permit_version,
                existing.token,
                existing.issued_at,
                existing.expires_at,
            )
            if current != identity:
                raise ValueError("offline permit changed for an existing work item")
            return existing
        row = OfflineStartPermitRow(
            permit_id=str(permit_id),
            work_item_id=str(work_item_id),
            attempt_id=str(attempt_id),
            package_id=str(package_id),
            package_hash=package_hash,
            permit_version=permit_version,
            token=token,
            status=OfflinePermitStatus.ISSUED.value,
            issued_at=issued_at,
            expires_at=expires_at,
            consumed_at=None,
            consumed_start_report_id=None,
            revoked_at=None,
            row_version=1,
        )
        self._session.add(row)
        self._session.flush()
        return _offline_permit(row)

    def consume(
        self,
        work_item_id: UUID,
        *,
        start_report_id: UUID,
        now: datetime,
    ) -> OfflineStartPermitRecord:
        row = self._session.scalar(
            select(OfflineStartPermitRow).where(
                OfflineStartPermitRow.work_item_id == str(work_item_id)
            )
        )
        if row is None:
            raise KeyError(work_item_id)
        if row.status == OfflinePermitStatus.CONSUMED.value:
            if row.consumed_start_report_id != str(start_report_id):
                raise ValueError("offline permit was consumed by another start report")
            return _offline_permit(row)
        if row.status != OfflinePermitStatus.ISSUED.value or now >= _as_utc(row.expires_at):
            raise ValueError("offline permit is not usable")
        row.status = OfflinePermitStatus.CONSUMED.value
        row.consumed_at = now
        row.consumed_start_report_id = str(start_report_id)
        row.row_version += 1
        self._session.flush()
        return _offline_permit(row)

    def revoke_unconsumed(
        self,
        work_item_id: UUID,
        *,
        now: datetime,
    ) -> OfflineStartPermitRecord:
        row = self._session.scalar(
            select(OfflineStartPermitRow).where(
                OfflineStartPermitRow.work_item_id == str(work_item_id)
            )
        )
        if row is None:
            raise KeyError(work_item_id)
        if row.status == OfflinePermitStatus.REVOKED.value:
            return _offline_permit(row)
        if row.status != OfflinePermitStatus.ISSUED.value:
            raise ValueError("only an unconsumed offline permit can be revoked")
        row.status = OfflinePermitStatus.REVOKED.value
        row.revoked_at = now
        row.row_version += 1
        self._session.flush()
        return _offline_permit(row)
