from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class RegistrationStatus(StrEnum):
    UNREGISTERED = "unregistered"
    REGISTERED = "registered"
    CREDENTIAL_REJECTED = "credential_rejected"


class WorkItemKind(StrEnum):
    FORMAL_TASK = "formal_task"
    QUICK_TEST = "quick_test"


class WorkItemStatus(StrEnum):
    RECEIVING = "receiving"
    QUEUED = "queued"
    RUNNING = "running"
    RESULT_PENDING = "result_pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    REJECTED = "rejected"


class ReportKind(StrEnum):
    PACKAGE_RECEIPT = "package_receipt"
    ATTEMPT_START = "attempt_start"
    ATTEMPT_RESULT = "attempt_result"
    QUICK_TEST_RESULT = "quick_test_result"


@dataclass(frozen=True, slots=True)
class QuickTestEventRecord:
    session_id: UUID
    sequence: int
    work_item_id: UUID
    kind: str
    step_number: int | None
    code: str | None
    created_at: datetime


class OutboxStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    CONFIRMED = "confirmed"
    DEAD_LETTER = "dead_letter"


class PackageManifestStatus(StrEnum):
    RECEIVING = "receiving"
    READY = "ready"
    REJECTED = "rejected"


class CachedResourceStatus(StrEnum):
    DOWNLOADING = "downloading"
    READY = "ready"
    QUARANTINED = "quarantined"


class AdbDeviceState(StrEnum):
    ONLINE = "device"
    OFFLINE = "offline"
    UNAUTHORIZED = "unauthorized"
    OTHER = "other"


class LocalArtifactStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    CONFIRMED = "confirmed"
    DEAD_LETTER = "dead_letter"


class OfflinePermitStatus(StrEnum):
    ISSUED = "issued"
    CONSUMED = "consumed"
    REVOKED = "revoked"
    EXPIRED = "expired"


class CancellationOutcome(StrEnum):
    RUNNING_CANCEL_ACCEPTED = "running_cancel_accepted"
    ALREADY_COMPLETED = "already_completed"
    CANCELLED_BEFORE_START = "cancelled_before_start"


@dataclass(frozen=True, slots=True)
class InstallationRecord:
    installation_id: UUID
    terminal_id: UUID | None
    registration_status: RegistrationStatus
    terminal_row_version: int | None
    credential_epoch: int
    agent_version: str
    capability_revision: int
    last_capability_hash: str | None
    row_version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class WorkItemRecord:
    work_item_id: UUID
    kind: WorkItemKind
    remote_id: UUID
    content_hash: str
    target_device_id: UUID | None
    status: WorkItemStatus
    available_at: datetime
    payload: dict[str, Any]
    lease_id: UUID | None
    lease_version: int | None
    cancel_command_id: UUID | None
    cancel_requested_at: datetime | None
    cancel_reason: str | None
    cancel_outcome: CancellationOutcome | None
    created_at: datetime
    updated_at: datetime
    row_version: int


@dataclass(frozen=True, slots=True)
class OutboxReportRecord:
    report_id: UUID
    kind: ReportKind
    work_item_id: UUID | None
    payload: dict[str, Any]
    status: OutboxStatus
    attempt_count: int
    available_at: datetime
    claimed_by: str | None
    claimed_until: datetime | None
    confirmed_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    row_version: int


@dataclass(frozen=True, slots=True)
class SecureIdentity:
    installation_id: UUID
    terminal_id: UUID
    credential: str
    credential_epoch: int
    terminal_row_version: int


@dataclass(frozen=True, slots=True)
class PackageManifestRecord:
    package_id: UUID
    work_item_id: UUID
    attempt_id: UUID
    execution_id: UUID
    package_hash: str
    protocol_version: int
    package_schema_version: int
    snapshot_schema_version: int
    relative_path: str | None
    status: PackageManifestStatus
    manifest: dict[str, Any]
    created_at: datetime
    ready_at: datetime | None
    row_version: int


@dataclass(frozen=True, slots=True)
class CachedResourceRecord:
    sha256: str
    blob_id: UUID
    size_bytes: int
    media_type: str
    relative_path: str
    status: CachedResourceStatus
    verified_at: datetime | None
    created_at: datetime
    updated_at: datetime
    row_version: int


@dataclass(frozen=True, slots=True)
class WorkItemResourceRecord:
    work_item_id: UUID
    resource_key: str
    blob_id: UUID
    sha256: str
    role: str
    relative_path: str
    media_type: str


@dataclass(frozen=True, slots=True)
class LocalArtifactRecord:
    artifact_id: UUID
    work_item_id: UUID
    owner_kind: str
    owner_id: UUID
    artifact_kind: str
    file_name: str
    relative_path: str
    sha256: str
    size_bytes: int
    media_type: str
    status: LocalArtifactStatus
    attempt_count: int
    available_at: datetime
    claimed_by: str | None
    claimed_until: datetime | None
    platform_artifact_id: UUID | None
    platform_blob_id: UUID | None
    confirmed_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    row_version: int


@dataclass(frozen=True, slots=True)
class NewLocalArtifact:
    artifact_id: UUID
    work_item_id: UUID
    owner_kind: str
    owner_id: UUID
    artifact_kind: str
    file_name: str
    relative_path: str
    sha256: str
    size_bytes: int
    media_type: str


@dataclass(frozen=True, slots=True)
class TargetDeviceObservationRecord:
    adb_serial: str
    identifier_id: UUID
    target_device_id: UUID | None
    identifier_row_version: int
    adb_state: AdbDeviceState
    model: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    row_version: int


@dataclass(frozen=True, slots=True)
class OfflineStartPermitRecord:
    permit_id: UUID
    work_item_id: UUID
    attempt_id: UUID
    package_id: UUID
    package_hash: str
    permit_version: int
    token: str
    status: OfflinePermitStatus
    issued_at: datetime
    expires_at: datetime
    consumed_at: datetime | None
    consumed_start_report_id: UUID | None
    revoked_at: datetime | None
    row_version: int
