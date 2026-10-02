from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from al1s_terminal.persistence.models import (
    CachedResourceRow,
    WorkItemResourceRow,
)
from al1s_terminal.persistence.repository_records import (
    _cached_resource,
)
from al1s_terminal.types import (
    CachedResourceRecord,
    CachedResourceStatus,
    WorkItemResourceRecord,
)


class CachedResourceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, sha256: str) -> CachedResourceRecord | None:
        row = self._session.get(CachedResourceRow, sha256)
        return None if row is None else _cached_resource(row)

    def ensure_downloading(
        self,
        *,
        sha256: str,
        blob_id: UUID,
        size_bytes: int,
        media_type: str,
        relative_path: str,
        now: datetime,
    ) -> CachedResourceRecord:
        existing = self.get(sha256)
        if existing is not None:
            if (
                existing.blob_id != blob_id
                or existing.size_bytes != size_bytes
                or existing.media_type != media_type
                or existing.relative_path != relative_path
            ):
                raise ValueError("resource hash was reused with different metadata")
            return existing
        row = CachedResourceRow(
            sha256=sha256,
            blob_id=str(blob_id),
            size_bytes=size_bytes,
            media_type=media_type,
            relative_path=relative_path,
            status=CachedResourceStatus.DOWNLOADING.value,
            verified_at=None,
            created_at=now,
            updated_at=now,
            row_version=1,
        )
        self._session.add(row)
        self._session.flush()
        return _cached_resource(row)

    def mark_ready(self, sha256: str, *, now: datetime) -> CachedResourceRecord:
        row = self._session.get(CachedResourceRow, sha256)
        if row is None:
            raise KeyError(sha256)
        if row.status == CachedResourceStatus.READY.value:
            return _cached_resource(row)
        if row.status != CachedResourceStatus.DOWNLOADING.value:
            raise ValueError(f"cannot ready resource from {row.status}")
        row.status = CachedResourceStatus.READY.value
        row.verified_at = now
        row.updated_at = now
        row.row_version += 1
        self._session.flush()
        return _cached_resource(row)

    def add_work_item_references(
        self,
        work_item_id: UUID,
        references: tuple[tuple[str, UUID, str, str], ...],
    ) -> None:
        if not references:
            return
        rows = tuple(
            self._session.scalars(
                select(WorkItemResourceRow).where(
                    WorkItemResourceRow.work_item_id == str(work_item_id)
                )
            )
        )
        existing = {row.resource_key: row for row in rows}
        for resource_key, blob_id, sha256, role in references:
            row = existing.get(resource_key)
            if row is not None:
                if row.blob_id != str(blob_id) or row.sha256 != sha256 or row.role != role:
                    raise ValueError("work item resource key was reused with different content")
                continue
            self._session.add(
                WorkItemResourceRow(
                    work_item_id=str(work_item_id),
                    resource_key=resource_key,
                    blob_id=str(blob_id),
                    sha256=sha256,
                    role=role,
                )
            )
        self._session.flush()

    def list_for_work_item(self, work_item_id: UUID) -> tuple[WorkItemResourceRecord, ...]:
        rows = self._session.execute(
            select(WorkItemResourceRow, CachedResourceRow)
            .join(CachedResourceRow, CachedResourceRow.sha256 == WorkItemResourceRow.sha256)
            .where(
                WorkItemResourceRow.work_item_id == str(work_item_id),
                CachedResourceRow.status == CachedResourceStatus.READY.value,
            )
            .order_by(WorkItemResourceRow.resource_key)
        ).all()
        return tuple(
            WorkItemResourceRecord(
                work_item_id=UUID(reference.work_item_id),
                resource_key=reference.resource_key,
                blob_id=UUID(reference.blob_id),
                sha256=reference.sha256,
                role=reference.role,
                relative_path=resource.relative_path,
                media_type=resource.media_type,
            )
            for reference, resource in rows
        )
