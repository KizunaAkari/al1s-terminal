from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from al1s_terminal.persistence.models import (
    CachedResourceRow,
    InboxWorkItemRow,
    LocalArtifactRow,
    OfflineStartPermitRow,
    OutboxReportRow,
    PackageManifestRow,
    TargetDeviceObservationRow,
    TerminalInstallationRow,
)
from al1s_terminal.types import (
    AdbDeviceState,
    CachedResourceRecord,
    CachedResourceStatus,
    CancellationOutcome,
    InstallationRecord,
    LocalArtifactRecord,
    LocalArtifactStatus,
    NewLocalArtifact,
    OfflinePermitStatus,
    OfflineStartPermitRecord,
    OutboxReportRecord,
    OutboxStatus,
    PackageManifestRecord,
    PackageManifestStatus,
    RegistrationStatus,
    ReportKind,
    TargetDeviceObservationRecord,
    WorkItemKind,
    WorkItemRecord,
    WorkItemStatus,
)


def _installation(row: TerminalInstallationRow) -> InstallationRecord:
    return InstallationRecord(
        installation_id=UUID(row.installation_id),
        terminal_id=UUID(row.terminal_id) if row.terminal_id else None,
        registration_status=RegistrationStatus(row.registration_status),
        terminal_row_version=row.terminal_row_version,
        credential_epoch=row.credential_epoch,
        agent_version=row.agent_version,
        capability_revision=row.capability_revision,
        last_capability_hash=row.last_capability_hash,
        row_version=row.row_version,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
    )

def _work_item(row: InboxWorkItemRow) -> WorkItemRecord:
    return WorkItemRecord(
        work_item_id=UUID(row.id),
        kind=WorkItemKind(row.kind),
        remote_id=UUID(row.remote_id),
        content_hash=row.content_hash,
        target_device_id=UUID(row.target_device_id) if row.target_device_id else None,
        status=WorkItemStatus(row.status),
        available_at=_as_utc(row.available_at),
        payload=dict(row.payload),
        lease_id=UUID(row.lease_id) if row.lease_id else None,
        lease_version=row.lease_version,
        cancel_command_id=UUID(row.cancel_command_id) if row.cancel_command_id else None,
        cancel_requested_at=(_as_utc(row.cancel_requested_at) if row.cancel_requested_at else None),
        cancel_reason=row.cancel_reason,
        cancel_outcome=(CancellationOutcome(row.cancel_outcome) if row.cancel_outcome else None),
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        row_version=row.row_version,
    )

def _outbox(row: OutboxReportRow) -> OutboxReportRecord:
    return OutboxReportRecord(
        report_id=UUID(row.report_id),
        kind=ReportKind(row.kind),
        work_item_id=UUID(row.work_item_id) if row.work_item_id else None,
        payload=dict(row.payload),
        status=OutboxStatus(row.status),
        attempt_count=row.attempt_count,
        available_at=_as_utc(row.available_at),
        claimed_by=row.claimed_by,
        claimed_until=_as_utc(row.claimed_until) if row.claimed_until else None,
        confirmed_at=_as_utc(row.confirmed_at) if row.confirmed_at else None,
        last_error_code=row.last_error_code,
        created_at=_as_utc(row.created_at),
        row_version=row.row_version,
    )

def _package_manifest(row: PackageManifestRow) -> PackageManifestRecord:
    return PackageManifestRecord(
        package_id=UUID(row.package_id),
        work_item_id=UUID(row.work_item_id),
        attempt_id=UUID(row.attempt_id),
        execution_id=UUID(row.execution_id),
        package_hash=row.package_hash,
        protocol_version=row.protocol_version,
        package_schema_version=row.package_schema_version,
        snapshot_schema_version=row.snapshot_schema_version,
        relative_path=row.relative_path,
        status=PackageManifestStatus(row.status),
        manifest=dict(row.manifest),
        created_at=_as_utc(row.created_at),
        ready_at=_as_utc(row.ready_at) if row.ready_at else None,
        row_version=row.row_version,
    )

def _cached_resource(row: CachedResourceRow) -> CachedResourceRecord:
    return CachedResourceRecord(
        sha256=row.sha256,
        blob_id=UUID(row.blob_id),
        size_bytes=row.size_bytes,
        media_type=row.media_type,
        relative_path=row.relative_path,
        status=CachedResourceStatus(row.status),
        verified_at=_as_utc(row.verified_at) if row.verified_at else None,
        created_at=_as_utc(row.created_at),
        updated_at=_as_utc(row.updated_at),
        row_version=row.row_version,
    )

def _target_observation(row: TargetDeviceObservationRow) -> TargetDeviceObservationRecord:
    return TargetDeviceObservationRecord(
        adb_serial=row.adb_serial,
        identifier_id=UUID(row.identifier_id),
        target_device_id=UUID(row.target_device_id) if row.target_device_id else None,
        identifier_row_version=row.identifier_row_version,
        adb_state=AdbDeviceState(row.adb_state),
        model=row.model,
        first_seen_at=_as_utc(row.first_seen_at),
        last_seen_at=_as_utc(row.last_seen_at),
        row_version=row.row_version,
    )

def _local_artifact(row: LocalArtifactRow) -> LocalArtifactRecord:
    return LocalArtifactRecord(
        artifact_id=UUID(row.artifact_id),
        work_item_id=UUID(row.work_item_id),
        owner_kind=row.owner_kind,
        owner_id=UUID(row.owner_id),
        artifact_kind=row.artifact_kind,
        file_name=row.file_name,
        relative_path=row.relative_path,
        sha256=row.sha256,
        size_bytes=row.size_bytes,
        media_type=row.media_type,
        status=LocalArtifactStatus(row.status),
        attempt_count=row.attempt_count,
        available_at=_as_utc(row.available_at),
        claimed_by=row.claimed_by,
        claimed_until=_as_utc(row.claimed_until) if row.claimed_until else None,
        platform_artifact_id=(UUID(row.platform_artifact_id) if row.platform_artifact_id else None),
        platform_blob_id=UUID(row.platform_blob_id) if row.platform_blob_id else None,
        confirmed_at=_as_utc(row.confirmed_at) if row.confirmed_at else None,
        last_error_code=row.last_error_code,
        created_at=_as_utc(row.created_at),
        row_version=row.row_version,
    )

def _offline_permit(row: OfflineStartPermitRow) -> OfflineStartPermitRecord:
    return OfflineStartPermitRecord(
        permit_id=UUID(row.permit_id),
        work_item_id=UUID(row.work_item_id),
        attempt_id=UUID(row.attempt_id),
        package_id=UUID(row.package_id),
        package_hash=row.package_hash,
        permit_version=row.permit_version,
        token=row.token,
        status=OfflinePermitStatus(row.status),
        issued_at=_as_utc(row.issued_at),
        expires_at=_as_utc(row.expires_at),
        consumed_at=_as_utc(row.consumed_at) if row.consumed_at else None,
        consumed_start_report_id=(
            UUID(row.consumed_start_report_id) if row.consumed_start_report_id else None
        ),
        revoked_at=_as_utc(row.revoked_at) if row.revoked_at else None,
        row_version=row.row_version,
    )

def _new_artifact_identity(artifact: NewLocalArtifact) -> tuple[object, ...]:
    return (
        str(artifact.work_item_id),
        artifact.owner_kind,
        str(artifact.owner_id),
        artifact.artifact_kind,
        artifact.file_name,
        artifact.relative_path,
        artifact.sha256,
        artifact.size_bytes,
        artifact.media_type,
    )

def _artifact_identity(row: LocalArtifactRow) -> tuple[object, ...]:
    return (
        row.work_item_id,
        row.owner_kind,
        row.owner_id,
        row.artifact_kind,
        row.file_name,
        row.relative_path,
        row.sha256,
        row.size_bytes,
        row.media_type,
    )

def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
