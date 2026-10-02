from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from al1s_terminal.persistence.models import (
    TargetDeviceObservationRow,
)
from al1s_terminal.persistence.repository_records import (
    _target_observation,
)
from al1s_terminal.types import (
    AdbDeviceState,
    TargetDeviceObservationRecord,
)


class TargetDeviceObservationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_serial(self, adb_serial: str) -> TargetDeviceObservationRecord | None:
        row = self._session.get(TargetDeviceObservationRow, adb_serial)
        return None if row is None else _target_observation(row)

    def get_by_serials(
        self, serials: tuple[str, ...]
    ) -> dict[str, TargetDeviceObservationRecord]:
        if not serials:
            return {}
        rows = self._session.scalars(
            select(TargetDeviceObservationRow).where(
                TargetDeviceObservationRow.adb_serial.in_(serials)
            )
        ).all()
        return {row.adb_serial: _target_observation(row) for row in rows}

    def get_by_target(self, target_device_id: UUID) -> TargetDeviceObservationRecord | None:
        rows = self._session.scalars(
            select(TargetDeviceObservationRow)
            .where(
                TargetDeviceObservationRow.target_device_id == str(target_device_id),
            )
            .limit(2)
        ).all()
        if len(rows) > 1:
            raise ValueError("target device has multiple local ADB identifiers")
        return None if not rows else _target_observation(rows[0])

    def record(
        self,
        *,
        adb_serial: str,
        identifier_id: UUID,
        target_device_id: UUID | None,
        identifier_row_version: int,
        adb_state: AdbDeviceState,
        model: str | None,
        now: datetime,
    ) -> TargetDeviceObservationRecord:
        row = self._session.get(TargetDeviceObservationRow, adb_serial)
        if row is None:
            row = TargetDeviceObservationRow(
                adb_serial=adb_serial,
                identifier_id=str(identifier_id),
                target_device_id=str(target_device_id) if target_device_id else None,
                identifier_row_version=identifier_row_version,
                adb_state=adb_state.value,
                model=model,
                first_seen_at=now,
                last_seen_at=now,
                row_version=1,
            )
            self._session.add(row)
        else:
            if row.identifier_id != str(identifier_id):
                raise ValueError("ADB serial was associated with a different platform identifier")
            row.target_device_id = str(target_device_id) if target_device_id else None
            row.identifier_row_version = identifier_row_version
            row.adb_state = adb_state.value
            row.model = model
            row.last_seen_at = now
            row.row_version += 1
        self._session.flush()
        return _target_observation(row)
