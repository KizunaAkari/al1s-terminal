from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from al1s_terminal.app.local_gc import LocalGarbageCollector, LocalGcUnitOfWork
from al1s_terminal.execution.artifact_store import LocalArtifactStore
from al1s_terminal.execution.content_store import ContentAddressedStore
from al1s_terminal.persistence.models import (
    CachedResourceRow,
    InboxWorkItemRow,
    LocalArtifactRow,
    LocalGcJobRow,
    OutboxReportRow,
    PackageManifestRow,
    WorkItemResourceRow,
)
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)
OLD = NOW - timedelta(days=2)


def test_gc_claim_does_not_authorize_deleting_a_newly_pinned_resource(local_engine, tmp_path):
    identifiers = _seed_completed_work(local_engine, tmp_path, outbox_status="pending")
    with Session(local_engine) as session:
        old = session.scalar(select(WorkItemResourceRow))
        values = (old.work_item_id, old.resource_key, old.blob_id, old.sha256, old.role)
        session.execute(delete(WorkItemResourceRow))
        session.commit()
    with _uow(local_engine) as uow:
        uow.gc.prepare_candidates(cutoff=NOW - timedelta(days=1), now=NOW, limit=20)
        claimed = uow.gc.claim_batch(
            worker_id="old", now=NOW, lease_duration=timedelta(seconds=1), limit=20
        )
        assert any(job.target_kind.value == "resource" for job in claimed)
    with Session(local_engine) as session:
        session.add(
            WorkItemResourceRow(
                work_item_id=values[0],
                resource_key=values[1],
                blob_id=values[2],
                sha256=values[3],
                role=values[4],
            )
        )
        session.commit()
    result = _collector(local_engine, tmp_path, lambda: NOW + timedelta(seconds=2)).collect_once()
    assert result.failed == 0
    assert identifiers["resource_path"].exists()
    with Session(local_engine) as session:
        assert session.get(CachedResourceRow, values[3]) is not None
        assert not session.scalars(
            select(LocalGcJobRow).where(LocalGcJobRow.target_kind == "resource")
        ).all()


def test_gc_deletes_confirmed_data_in_reference_safe_phases(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    identifiers = _seed_completed_work(local_engine, tmp_path)
    collector = _collector(local_engine, tmp_path, lambda: NOW)

    first = collector.collect_once()
    assert first.deleted == 1
    assert not identifiers["artifact_path"].exists()
    assert identifiers["package_path"].exists()
    assert identifiers["resource_path"].exists()

    second = collector.collect_once()
    assert second.deleted == 1
    assert not identifiers["package_path"].exists()
    assert identifiers["resource_path"].exists()

    third = collector.collect_once()
    assert third.deleted == 1
    assert not identifiers["resource_path"].exists()
    with Session(local_engine) as session:
        assert session.scalar(select(InboxWorkItemRow)) is None
        assert session.scalar(select(LocalArtifactRow)) is None
        assert session.scalar(select(CachedResourceRow)) is None
        assert session.scalar(select(LocalGcJobRow)) is None


def test_gc_never_deletes_unconfirmed_or_actively_referenced_data(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    identifiers = _seed_completed_work(local_engine, tmp_path, outbox_status="pending")
    collector = _collector(local_engine, tmp_path, lambda: NOW)

    first = collector.collect_once()
    second = collector.collect_once()

    assert first.deleted == 1  # confirmed artifact is independent after upload acknowledgement
    assert second.deleted == 0
    assert identifiers["package_path"].exists()
    assert identifiers["resource_path"].exists()
    with Session(local_engine) as session:
        assert session.scalar(select(InboxWorkItemRow)) is not None
        assert session.scalar(select(CachedResourceRow)) is not None


def test_gc_retries_after_file_was_deleted_before_database_finalization(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    identifiers = _seed_completed_work(local_engine, tmp_path)
    clock = [NOW]
    with _uow(local_engine) as uow:
        assert uow.gc.prepare_candidates(cutoff=NOW - timedelta(days=1), now=NOW, limit=1) == 1
    with _uow(local_engine) as uow:
        claimed = tuple(
            uow.gc.claim_batch(
                worker_id="crashed-worker",
                now=NOW,
                lease_duration=timedelta(minutes=2),
                limit=1,
            )
        )
    assert len(claimed) == 1
    identifiers["artifact_path"].unlink()
    clock[0] += timedelta(minutes=3)

    result = _collector(local_engine, tmp_path, lambda: clock[0]).collect_once()

    assert result.deleted == 1
    with Session(local_engine) as session:
        assert session.get(LocalArtifactRow, claimed[0].target_id) is None


def _collector(
    engine: Engine,
    tmp_path: Path,
    clock: Callable[[], datetime],
) -> LocalGarbageCollector:
    return LocalGarbageCollector(
        uow_factory=cast(Callable[[], LocalGcUnitOfWork], lambda: _uow(engine)),
        artifact_store=LocalArtifactStore(tmp_path),
        content_store=ContentAddressedStore(tmp_path),
        retention=timedelta(days=1),
        clock=clock,
    )


def _uow(engine: Engine) -> LocalUnitOfWork:
    return LocalUnitOfWork.from_engine(engine)


def _seed_completed_work(
    engine: Engine,
    tmp_path: Path,
    *,
    outbox_status: str = "confirmed",
) -> dict[str, Path]:
    work_item_id = uuid4()
    package_id = uuid4()
    attempt_id = uuid4()
    execution_id = uuid4()
    artifact_id = uuid4()
    content = ContentAddressedStore(tmp_path)
    package_relative = content.write_package(package_id, {"protocol_version": 1})
    resource_body = b"resource"
    sha256 = hashlib.sha256(resource_body).hexdigest()
    resource_relative = content.ensure_blob(
        sha256=sha256,
        size_bytes=len(resource_body),
        download_range=lambda start, end: resource_body[start : end + 1],
    )
    source = tmp_path / "runtime" / "failure.png"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"failure")
    artifact = LocalArtifactStore(tmp_path).stage(
        artifact_id=artifact_id,
        work_item_id=work_item_id,
        owner_kind="formal_attempt",
        owner_id=attempt_id,
        artifact_kind="screenshot",
        file_name="failure.png",
        media_type="image/png",
        source=source,
    )
    with Session(engine) as session:
        session.add(
            InboxWorkItemRow(
                id=str(work_item_id),
                kind="formal_task",
                remote_id=str(package_id),
                content_hash="a" * 64,
                target_device_id=None,
                status="completed",
                available_at=OLD,
                queue_enqueued_at=OLD,
                queue_order_id=str(work_item_id),
                payload={},
                created_at=OLD,
                updated_at=OLD,
                row_version=1,
            )
        )
        session.add(
            PackageManifestRow(
                package_id=str(package_id),
                work_item_id=str(work_item_id),
                attempt_id=str(attempt_id),
                execution_id=str(execution_id),
                package_hash="b" * 64,
                protocol_version=1,
                package_schema_version=1,
                snapshot_schema_version=1,
                relative_path=package_relative,
                status="ready",
                manifest={},
                created_at=OLD,
                ready_at=OLD,
                row_version=1,
            )
        )
        session.add(
            CachedResourceRow(
                sha256=sha256,
                blob_id=str(uuid4()),
                size_bytes=len(resource_body),
                media_type="application/octet-stream",
                relative_path=resource_relative,
                status="ready",
                verified_at=OLD,
                created_at=OLD,
                updated_at=OLD,
                row_version=1,
            )
        )
        session.add(
            WorkItemResourceRow(
                work_item_id=str(work_item_id),
                resource_key="definition",
                blob_id=str(uuid4()),
                sha256=sha256,
                role="definition",
            )
        )
        session.add(
            LocalArtifactRow(
                artifact_id=str(artifact_id),
                work_item_id=str(work_item_id),
                owner_kind=artifact.owner_kind,
                owner_id=str(artifact.owner_id),
                artifact_kind=artifact.artifact_kind,
                file_name=artifact.file_name,
                relative_path=artifact.relative_path,
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
                media_type=artifact.media_type,
                status="confirmed",
                attempt_count=1,
                available_at=OLD,
                platform_artifact_id=str(uuid4()),
                platform_blob_id=str(uuid4()),
                confirmed_at=OLD,
                created_at=OLD,
                row_version=2,
            )
        )
        session.add(
            OutboxReportRow(
                report_id=str(uuid4()),
                kind="attempt_result",
                work_item_id=str(work_item_id),
                payload={},
                status=outbox_status,
                attempt_count=1,
                available_at=OLD,
                confirmed_at=OLD if outbox_status == "confirmed" else None,
                created_at=OLD,
                row_version=1,
            )
        )
        session.commit()
    return {
        "artifact_path": tmp_path / artifact.relative_path,
        "package_path": tmp_path / package_relative,
        "resource_path": tmp_path / resource_relative,
    }
