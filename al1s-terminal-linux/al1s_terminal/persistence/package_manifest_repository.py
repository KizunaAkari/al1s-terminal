from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from al1s_terminal.persistence.models import (
    PackageManifestRow,
)
from al1s_terminal.persistence.repository_records import (
    _package_manifest,
)
from al1s_terminal.types import (
    PackageManifestRecord,
    PackageManifestStatus,
)


class PackageManifestRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, package_id: UUID) -> PackageManifestRecord | None:
        row = self._session.get(PackageManifestRow, str(package_id))
        return None if row is None else _package_manifest(row)

    def get_by_work_item(self, work_item_id: UUID) -> PackageManifestRecord | None:
        row = self._session.scalar(
            select(PackageManifestRow).where(PackageManifestRow.work_item_id == str(work_item_id))
        )
        return None if row is None else _package_manifest(row)

    def add_receiving(
        self,
        *,
        package_id: UUID,
        work_item_id: UUID,
        attempt_id: UUID,
        execution_id: UUID,
        package_hash: str,
        protocol_version: int,
        package_schema_version: int,
        snapshot_schema_version: int,
        manifest: dict[str, Any],
        now: datetime,
    ) -> PackageManifestRecord:
        existing = self.get(package_id)
        if existing is not None:
            if (
                existing.work_item_id != work_item_id
                or existing.attempt_id != attempt_id
                or existing.execution_id != execution_id
                or existing.package_hash != package_hash
                or existing.protocol_version != protocol_version
                or existing.package_schema_version != package_schema_version
                or existing.snapshot_schema_version != snapshot_schema_version
                or existing.manifest != manifest
            ):
                raise ValueError("package id was reused with different content")
            return existing
        row = PackageManifestRow(
            package_id=str(package_id),
            work_item_id=str(work_item_id),
            attempt_id=str(attempt_id),
            execution_id=str(execution_id),
            package_hash=package_hash,
            protocol_version=protocol_version,
            package_schema_version=package_schema_version,
            snapshot_schema_version=snapshot_schema_version,
            relative_path=None,
            status=PackageManifestStatus.RECEIVING.value,
            manifest=manifest,
            created_at=now,
            ready_at=None,
            row_version=1,
        )
        self._session.add(row)
        self._session.flush()
        return _package_manifest(row)

    def mark_ready(
        self,
        package_id: UUID,
        *,
        relative_path: str,
        now: datetime,
    ) -> PackageManifestRecord:
        row = self._session.get(PackageManifestRow, str(package_id))
        if row is None:
            raise KeyError(package_id)
        if row.status == PackageManifestStatus.READY.value:
            if row.relative_path != relative_path:
                raise ValueError("ready package path cannot change")
            return _package_manifest(row)
        if row.status != PackageManifestStatus.RECEIVING.value:
            raise ValueError(f"cannot ready package from {row.status}")
        row.status = PackageManifestStatus.READY.value
        row.relative_path = relative_path
        row.ready_at = now
        row.row_version += 1
        self._session.flush()
        return _package_manifest(row)
