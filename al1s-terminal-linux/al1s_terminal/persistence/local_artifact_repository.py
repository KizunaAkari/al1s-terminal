from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from al1s_terminal.persistence.models import (
    LocalArtifactRow,
)
from al1s_terminal.persistence.repository_records import (
    _artifact_identity,
    _local_artifact,
    _new_artifact_identity,
)
from al1s_terminal.types import (
    LocalArtifactRecord,
    LocalArtifactStatus,
    NewLocalArtifact,
)


class LocalArtifactRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, artifact_id: UUID) -> LocalArtifactRecord | None:
        row = self._session.get(LocalArtifactRow, str(artifact_id))
        return None if row is None else _local_artifact(row)

    def add(
        self,
        *,
        artifact_id: UUID,
        work_item_id: UUID,
        owner_kind: str,
        owner_id: UUID,
        artifact_kind: str,
        file_name: str,
        relative_path: str,
        sha256: str,
        size_bytes: int,
        media_type: str,
        now: datetime,
    ) -> LocalArtifactRecord:
        return self.add_many(
            (
                NewLocalArtifact(
                    artifact_id=artifact_id,
                    work_item_id=work_item_id,
                    owner_kind=owner_kind,
                    owner_id=owner_id,
                    artifact_kind=artifact_kind,
                    file_name=file_name,
                    relative_path=relative_path,
                    sha256=sha256,
                    size_bytes=size_bytes,
                    media_type=media_type,
                ),
            ),
            now=now,
        )[0]

    def add_many(
        self,
        artifacts: tuple[NewLocalArtifact, ...],
        *,
        now: datetime,
    ) -> tuple[LocalArtifactRecord, ...]:
        if not artifacts:
            return ()
        ids = [str(item.artifact_id) for item in artifacts]
        rows_by_id = {
            row.artifact_id: row
            for row in self._session.scalars(
                select(LocalArtifactRow).where(LocalArtifactRow.artifact_id.in_(ids))
            )
        }
        result: list[LocalArtifactRow] = []
        for artifact in artifacts:
            identity = _new_artifact_identity(artifact)
            existing = rows_by_id.get(str(artifact.artifact_id))
            if existing is not None:
                if _artifact_identity(existing) != identity:
                    raise ValueError("local artifact id was reused with different content")
                result.append(existing)
                continue
            row = LocalArtifactRow(
                artifact_id=str(artifact.artifact_id),
                work_item_id=str(artifact.work_item_id),
                owner_kind=artifact.owner_kind,
                owner_id=str(artifact.owner_id),
                artifact_kind=artifact.artifact_kind,
                file_name=artifact.file_name,
                relative_path=artifact.relative_path,
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
                media_type=artifact.media_type,
                status=LocalArtifactStatus.PENDING.value,
                attempt_count=0,
                available_at=now,
                claimed_by=None,
                claimed_until=None,
                platform_artifact_id=None,
                platform_blob_id=None,
                confirmed_at=None,
                last_error_code=None,
                created_at=now,
                row_version=1,
            )
            self._session.add(row)
            rows_by_id[row.artifact_id] = row
            result.append(row)
        self._session.flush()
        return tuple(_local_artifact(row) for row in result)

    def claim_batch(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> tuple[LocalArtifactRecord, ...]:
        if not 1 <= limit <= 20:
            raise ValueError("artifact upload limit must be between 1 and 20")
        rows = tuple(
            self._session.scalars(
                select(LocalArtifactRow)
                .where(
                    LocalArtifactRow.available_at <= now,
                    or_(
                        LocalArtifactRow.status == LocalArtifactStatus.PENDING.value,
                        and_(
                            LocalArtifactRow.status == LocalArtifactStatus.CLAIMED.value,
                            LocalArtifactRow.claimed_until <= now,
                        ),
                    ),
                )
                .order_by(
                    LocalArtifactRow.available_at,
                    LocalArtifactRow.created_at,
                    LocalArtifactRow.artifact_id,
                )
                .limit(limit)
            )
        )
        claimed_until = now + lease_duration
        for row in rows:
            row.status = LocalArtifactStatus.CLAIMED.value
            row.claimed_by = worker_id
            row.claimed_until = claimed_until
            row.attempt_count += 1
            row.row_version += 1
        self._session.flush()
        return tuple(_local_artifact(row) for row in rows)

    def confirm(
        self,
        artifact_id: UUID,
        *,
        worker_id: str,
        platform_artifact_id: UUID,
        platform_blob_id: UUID,
        now: datetime,
    ) -> bool:
        result = self._session.execute(
            update(LocalArtifactRow)
            .where(
                LocalArtifactRow.artifact_id == str(artifact_id),
                LocalArtifactRow.status == LocalArtifactStatus.CLAIMED.value,
                LocalArtifactRow.claimed_by == worker_id,
            )
            .values(
                status=LocalArtifactStatus.CONFIRMED.value,
                claimed_by=None,
                claimed_until=None,
                platform_artifact_id=str(platform_artifact_id),
                platform_blob_id=str(platform_blob_id),
                confirmed_at=now,
                row_version=LocalArtifactRow.row_version + 1,
            )
            .returning(LocalArtifactRow.artifact_id)
        )
        return result.scalar_one_or_none() is not None

    def release_failed(
        self,
        artifact_id: UUID,
        *,
        worker_id: str,
        available_at: datetime,
        error_code: str,
        permanent: bool = False,
    ) -> bool:
        next_status = (
            LocalArtifactStatus.DEAD_LETTER.value
            if permanent
            else LocalArtifactStatus.PENDING.value
        )
        result = self._session.execute(
            update(LocalArtifactRow)
            .where(
                LocalArtifactRow.artifact_id == str(artifact_id),
                LocalArtifactRow.status == LocalArtifactStatus.CLAIMED.value,
                LocalArtifactRow.claimed_by == worker_id,
            )
            .values(
                status=next_status,
                available_at=available_at,
                claimed_by=None,
                claimed_until=None,
                last_error_code=error_code[:100],
                row_version=LocalArtifactRow.row_version + 1,
            )
            .returning(LocalArtifactRow.artifact_id)
        )
        return result.scalar_one_or_none() is not None
