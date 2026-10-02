from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import case, delete, exists, func, or_, select, tuple_, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from al1s_terminal.gc_types import (
    ClaimedLocalGcJob,
    FailedLocalGcJob,
    LocalGcTargetKind,
)
from al1s_terminal.persistence.models import (
    CachedResourceRow,
    InboxWorkItemRow,
    LocalArtifactRow,
    LocalGcJobRow,
    LocalTransitionRow,
    OfflineStartPermitRow,
    OutboxReportRow,
    PackageManifestRow,
    WorkItemResourceRow,
)
from al1s_terminal.types import LocalArtifactStatus, OutboxStatus, WorkItemStatus

_TERMINAL_WORK_STATUSES = (
    WorkItemStatus.COMPLETED.value,
    WorkItemStatus.CANCELLED.value,
    WorkItemStatus.INTERRUPTED.value,
    WorkItemStatus.REJECTED.value,
)


class LocalGcRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def prepare_candidates(self, *, cutoff: datetime, now: datetime, limit: int) -> int:
        prepared = self._prepare_artifacts(cutoff=cutoff, now=now, limit=limit)
        remaining = limit - prepared
        if remaining:
            count = self._prepare_work_items(cutoff=cutoff, now=now, limit=remaining)
            prepared += count
            remaining -= count
        if remaining:
            prepared += self._prepare_resources(cutoff=cutoff, now=now, limit=remaining)
        return prepared

    def claim_batch(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> Sequence[ClaimedLocalGcJob]:
        claimable = (
            select(LocalGcJobRow.job_id)
            .where(
                or_(
                    (LocalGcJobRow.status == "pending") & (LocalGcJobRow.available_at <= now),
                    (LocalGcJobRow.status == "claimed") & (LocalGcJobRow.claimed_until <= now),
                )
            )
            .order_by(LocalGcJobRow.available_at, LocalGcJobRow.created_at, LocalGcJobRow.job_id)
            .limit(limit)
            .cte("claimable_local_gc")
        )
        rows = self._session.execute(
            update(LocalGcJobRow)
            .where(LocalGcJobRow.job_id.in_(select(claimable.c.job_id)))
            .values(
                status="claimed",
                claimed_by=worker_id,
                claimed_until=now + lease_duration,
                attempt_count=LocalGcJobRow.attempt_count + 1,
                row_version=LocalGcJobRow.row_version + 1,
            )
            .returning(
                LocalGcJobRow.job_id,
                LocalGcJobRow.target_kind,
                LocalGcJobRow.target_id,
                LocalGcJobRow.relative_path,
                LocalGcJobRow.attempt_count,
                LocalGcJobRow.row_version,
            )
        ).all()
        return tuple(
            ClaimedLocalGcJob(
                job_id=UUID(row.job_id),
                target_kind=LocalGcTargetKind(row.target_kind),
                target_id=row.target_id,
                relative_path=row.relative_path,
                attempt_count=row.attempt_count,
                row_version=row.row_version,
            )
            for row in rows
        )

    def protect_deletions(
        self,
        jobs: Sequence[ClaimedLocalGcJob],
        *,
        worker_id: str,
        now: datetime,
    ) -> tuple[ClaimedLocalGcJob, ...]:
        """Hold SQLite's writer lock through unlink and settlement; recheck new pins."""
        if not jobs:
            return ()
        owned = set(
            self._session.scalars(
                update(LocalGcJobRow)
                .where(
                    tuple_(LocalGcJobRow.job_id, LocalGcJobRow.row_version).in_(
                        [(str(job.job_id), job.row_version) for job in jobs]
                    ),
                    LocalGcJobRow.status == "claimed",
                    LocalGcJobRow.claimed_by == worker_id,
                    LocalGcJobRow.claimed_until > now,
                )
                .values(claimed_by=worker_id)
                .returning(LocalGcJobRow.job_id)
            )
        )
        hashes = [job.target_id for job in jobs if job.target_kind is LocalGcTargetKind.RESOURCE]
        pinned = (
            set(
                self._session.scalars(
                    select(WorkItemResourceRow.sha256).where(WorkItemResourceRow.sha256.in_(hashes))
                )
            )
            if hashes
            else set()
        )
        protected = [
            str(job.job_id)
            for job in jobs
            if str(job.job_id) in owned
            and job.target_kind is LocalGcTargetKind.RESOURCE
            and job.target_id in pinned
        ]
        if protected:
            self._session.execute(delete(LocalGcJobRow).where(LocalGcJobRow.job_id.in_(protected)))
        return tuple(
            job for job in jobs if str(job.job_id) in owned and str(job.job_id) not in protected
        )

    def settle(
        self,
        *,
        worker_id: str,
        succeeded: Sequence[ClaimedLocalGcJob],
        failed: Sequence[FailedLocalGcJob],
        now: datetime,
        max_attempts: int,
    ) -> tuple[int, int]:
        verified = self._verified_jobs(worker_id, succeeded, failed)
        success = [job for job in succeeded if job.job_id in verified]
        failures = [item for item in failed if item.job.job_id in verified]
        self._delete_targets(success)
        if success:
            self._session.execute(
                delete(LocalGcJobRow).where(
                    LocalGcJobRow.job_id.in_(str(job.job_id) for job in success)
                )
            )
        self._release_failures(worker_id, failures, now=now, max_attempts=max_attempts)
        return len(success), len(failures)

    def _prepare_artifacts(self, *, cutoff: datetime, now: datetime, limit: int) -> int:
        rows = self._session.execute(
            select(LocalArtifactRow.artifact_id, LocalArtifactRow.relative_path)
            .join(InboxWorkItemRow, InboxWorkItemRow.id == LocalArtifactRow.work_item_id)
            .where(
                LocalArtifactRow.status == LocalArtifactStatus.CONFIRMED.value,
                LocalArtifactRow.confirmed_at <= cutoff,
                InboxWorkItemRow.status.in_(_TERMINAL_WORK_STATUSES),
                ~self._job_exists("artifact", LocalArtifactRow.artifact_id),
            )
            .order_by(LocalArtifactRow.confirmed_at, LocalArtifactRow.artifact_id)
            .limit(limit)
        ).all()
        return self._insert_jobs("artifact", rows, now=now)

    def _prepare_work_items(self, *, cutoff: datetime, now: datetime, limit: int) -> int:
        definition_path = InboxWorkItemRow.payload["definition_path"].as_string()
        rows = self._session.execute(
            select(
                InboxWorkItemRow.id,
                func.coalesce(PackageManifestRow.relative_path, definition_path),
            )
            .outerjoin(PackageManifestRow, PackageManifestRow.work_item_id == InboxWorkItemRow.id)
            .where(
                InboxWorkItemRow.status.in_(_TERMINAL_WORK_STATUSES),
                InboxWorkItemRow.updated_at <= cutoff,
                ~exists().where(LocalArtifactRow.work_item_id == InboxWorkItemRow.id),
                ~exists().where(
                    OutboxReportRow.work_item_id == InboxWorkItemRow.id,
                    OutboxReportRow.status != OutboxStatus.CONFIRMED.value,
                ),
                ~self._job_exists("work_item", InboxWorkItemRow.id),
            )
            .order_by(InboxWorkItemRow.updated_at, InboxWorkItemRow.id)
            .limit(limit)
        ).all()
        return self._insert_jobs("work_item", rows, now=now)

    def _prepare_resources(self, *, cutoff: datetime, now: datetime, limit: int) -> int:
        rows = self._session.execute(
            select(CachedResourceRow.sha256, CachedResourceRow.relative_path)
            .where(
                CachedResourceRow.status.in_(("ready", "quarantined")),
                CachedResourceRow.updated_at <= cutoff,
                ~exists().where(WorkItemResourceRow.sha256 == CachedResourceRow.sha256),
                ~self._job_exists("resource", CachedResourceRow.sha256),
            )
            .order_by(CachedResourceRow.updated_at, CachedResourceRow.sha256)
            .limit(limit)
        ).all()
        return self._insert_jobs("resource", rows, now=now)

    def _job_exists(self, target_kind: str, target_id: object) -> ColumnElement[bool]:
        return exists().where(
            LocalGcJobRow.target_kind == target_kind,
            LocalGcJobRow.target_id == target_id,
        )

    def _insert_jobs(self, target_kind: str, rows: Sequence[object], *, now: datetime) -> int:
        values = [
            {
                "job_id": str(uuid4()),
                "target_kind": target_kind,
                "target_id": str(row[0]),  # type: ignore[index]
                "relative_path": row[1],  # type: ignore[index]
                "status": "pending",
                "attempt_count": 0,
                "available_at": now,
                "created_at": now,
                "row_version": 1,
            }
            for row in rows
        ]
        if not values:
            return 0
        inserted = self._session.scalars(
            sqlite_insert(LocalGcJobRow)
            .values(values)
            .on_conflict_do_nothing(index_elements=["target_kind", "target_id"])
            .returning(LocalGcJobRow.job_id)
        ).all()
        return len(inserted)

    def _verified_jobs(
        self,
        worker_id: str,
        succeeded: Sequence[ClaimedLocalGcJob],
        failed: Sequence[FailedLocalGcJob],
    ) -> set[UUID]:
        expected = [(str(job.job_id), job.row_version) for job in succeeded]
        expected.extend((str(item.job.job_id), item.job.row_version) for item in failed)
        if not expected:
            return set()
        ids = self._session.scalars(
            select(LocalGcJobRow.job_id).where(
                tuple_(LocalGcJobRow.job_id, LocalGcJobRow.row_version).in_(expected),
                LocalGcJobRow.status == "claimed",
                LocalGcJobRow.claimed_by == worker_id,
            )
        ).all()
        return {UUID(value) for value in ids}

    def _delete_targets(self, jobs: Sequence[ClaimedLocalGcJob]) -> None:
        artifacts = [job.target_id for job in jobs if job.target_kind is LocalGcTargetKind.ARTIFACT]
        work_items = [
            job.target_id for job in jobs if job.target_kind is LocalGcTargetKind.WORK_ITEM
        ]
        resources = [job.target_id for job in jobs if job.target_kind is LocalGcTargetKind.RESOURCE]
        if artifacts:
            self._session.execute(
                delete(LocalArtifactRow).where(LocalArtifactRow.artifact_id.in_(artifacts))
            )
        if work_items:
            self._delete_work_items(work_items)
        if resources:
            self._session.execute(
                delete(CachedResourceRow).where(CachedResourceRow.sha256.in_(resources))
            )

    def _delete_work_items(self, work_item_ids: Sequence[str]) -> None:
        self._session.execute(
            delete(OfflineStartPermitRow).where(
                OfflineStartPermitRow.work_item_id.in_(work_item_ids)
            )
        )
        self._session.execute(
            delete(WorkItemResourceRow).where(WorkItemResourceRow.work_item_id.in_(work_item_ids))
        )
        self._session.execute(
            delete(PackageManifestRow).where(PackageManifestRow.work_item_id.in_(work_item_ids))
        )
        self._session.execute(
            delete(LocalTransitionRow).where(LocalTransitionRow.work_item_id.in_(work_item_ids))
        )
        self._session.execute(
            delete(OutboxReportRow).where(
                OutboxReportRow.work_item_id.in_(work_item_ids),
                OutboxReportRow.status == OutboxStatus.CONFIRMED.value,
            )
        )
        self._session.execute(
            delete(InboxWorkItemRow).where(InboxWorkItemRow.id.in_(work_item_ids))
        )

    def _release_failures(
        self,
        worker_id: str,
        failures: Sequence[FailedLocalGcJob],
        *,
        now: datetime,
        max_attempts: int,
    ) -> None:
        if not failures:
            return
        statuses = {
            str(item.job.job_id): (
                "dead_letter" if item.job.attempt_count >= max_attempts else "pending"
            )
            for item in failures
        }
        errors = {str(item.job.job_id): item.error_code for item in failures}
        expected = [(str(item.job.job_id), item.job.row_version) for item in failures]
        self._session.execute(
            update(LocalGcJobRow)
            .where(
                tuple_(LocalGcJobRow.job_id, LocalGcJobRow.row_version).in_(expected),
                LocalGcJobRow.status == "claimed",
                LocalGcJobRow.claimed_by == worker_id,
            )
            .values(
                status=case(statuses, value=LocalGcJobRow.job_id),
                available_at=now + timedelta(minutes=1),
                claimed_by=None,
                claimed_until=None,
                last_error_code=case(errors, value=LocalGcJobRow.job_id),
                row_version=LocalGcJobRow.row_version + 1,
            )
        )
