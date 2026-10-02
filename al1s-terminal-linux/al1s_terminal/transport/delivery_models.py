from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class TerminalCommandPayload(BaseModel):
    command_id: UUID
    kind: str
    package_id: UUID | None
    attempt_id: UUID
    delivery_no: int = Field(ge=1)
    status: str
    payload: dict[str, Any]
    available_at: datetime
    created_at: datetime
    row_version: int = Field(ge=1)


class TerminalCommandPagePayload(BaseModel):
    items: list[TerminalCommandPayload]


class TerminalMqttSessionPayload(BaseModel):
    session_id: UUID
    broker_host: str
    broker_port: int = Field(ge=1, le=65_535)
    tls_enabled: bool
    client_id: str
    username: str
    password: str
    topic: str
    qos: int = Field(ge=0, le=2)
    issued_at: datetime
    expires_at: datetime


class CommandAcknowledgementPayload(BaseModel):
    protocol_version: int = 1
    report_id: UUID
    outcome: str
    occurred_at: datetime


class CommandAcknowledgementResultPayload(BaseModel):
    report_id: UUID
    disposition: str
    command_status: str
    execution_status: str


class PackageResourcePayload(BaseModel):
    blob_id: UUID
    resource_key: str = Field(min_length=1, max_length=255)
    role: str = Field(min_length=1, max_length=80)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=255)


class CapabilityRequirementsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1)
    min_protocol_version: int = Field(default=1, ge=1)
    architectures: list[str] = Field(default_factory=list, max_length=32)
    min_memory_bytes: int = Field(default=0, ge=0)
    min_storage_bytes: int = Field(default=0, ge=0)
    accelerator_type: str | None = Field(default=None, max_length=100)
    provider_keys: list[str] = Field(default_factory=list, max_length=128)
    requires_target_device: bool = False


class QueueOrderPayload(BaseModel):
    available_at: AwareDatetime
    enqueued_at: AwareDatetime


class TaskPackageBodyPayload(BaseModel):
    protocol_version: int = Field(ge=1)
    package_schema_version: int = Field(ge=1)
    snapshot_schema_version: int = Field(ge=1)
    package_id: UUID
    execution_id: UUID
    attempt_id: UUID
    queue_order: QueueOrderPayload | None = None
    snapshot_id: UUID
    terminal_id: UUID
    target_device_id: UUID | None
    capability_requirements: CapabilityRequirementsPayload
    resources: list[PackageResourcePayload]
    timeout_seconds: int = Field(ge=1, le=4 * 60 * 60)
    record_video: bool


class TaskPackagePayload(BaseModel):
    package_id: UUID
    attempt_id: UUID
    execution_id: UUID
    package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str
    body: dict[str, Any]
    row_version: int = Field(ge=1)


class TerminalBlobMetadataPayload(BaseModel):
    blob_id: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str


class PackageReceiptPayload(BaseModel):
    protocol_version: int = 1
    report_id: UUID
    command_id: UUID | None
    attempt_id: UUID
    disposition: str
    rejection_code: str | None = None
    diagnostic: str | None = None
    occurred_at: datetime


class PackageReceiptResultPayload(BaseModel):
    report_id: UUID
    disposition: str
    package_status: str
    execution_status: str
    rejection_code: str | None
    offline_start_permit: OfflineStartPermitPayload | None


class OfflineStartPermitPayload(BaseModel):
    permit_id: UUID
    permit_version: int = Field(ge=1)
    token: str = Field(min_length=1, max_length=255)
    issued_at: datetime
    expires_at: datetime


class AttemptStartPayload(BaseModel):
    protocol_version: int = 1
    report_id: UUID
    package_id: UUID
    offline_permit_id: UUID
    offline_permit_token: str = Field(min_length=1, max_length=255)
    occurred_at: datetime


class AttemptStartResultPayload(BaseModel):
    report_id: UUID
    disposition: str
    execution_status: str
    attempt_status: str
    lease_id: UUID
    lease_version: int = Field(ge=1)
    lease_expires_at: datetime


class AttemptResultPayload(BaseModel):
    diagnostic: dict[str, Any] | None = None
    protocol_version: int = 1
    report_id: UUID
    package_id: UUID
    lease_id: UUID
    lease_version: int = Field(ge=1)
    result: str
    error_code: str | None = Field(default=None, max_length=100)
    retryable: bool = False
    occurred_at: datetime


class AttemptResultReceiptPayload(BaseModel):
    report_id: UUID
    disposition: str
    execution_status: str
    attempt_status: str
    retry_attempt_id: UUID | None


class QuickTestSummaryPayload(BaseModel):
    session_id: UUID
    script_id: UUID
    candidate_version_id: UUID
    candidate_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    definition_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_device_id: UUID
    status: str
    expires_at: datetime
    row_version: int = Field(ge=1)


class QuickTestPagePayload(BaseModel):
    items: list[QuickTestSummaryPayload]


class QuickTestDefinitionBlobPayload(BaseModel):
    blob_id: UUID
    resource_key: str
    role: str


class QuickTestDefinitionPayload(BaseModel):
    revision_id: str
    schema_version: int = Field(ge=1)
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest: dict[str, Any]
    capability_requirements: CapabilityRequirementsPayload
    blobs: list[QuickTestDefinitionBlobPayload]


class QuickTestClaimPayload(BaseModel):
    session: QuickTestSummaryPayload
    definition: QuickTestDefinitionPayload


class QuickTestControlPayload(BaseModel):
    status: str
    cancel_requested: bool


class QuickTestEventPayload(BaseModel):
    sequence: int = Field(ge=1, le=1000)
    kind: str
    step_number: int | None = None
    code: str | None = None
    created_at: datetime


class QuickTestEventBatchPayload(BaseModel):
    items: list[QuickTestEventPayload] = Field(min_length=1, max_length=50)


class QuickTestEventReceiptPayload(BaseModel):
    last_sequence: int


class QuickTestResultPayload(BaseModel):
    session_id: UUID
    candidate_version_id: UUID
    candidate_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    definition_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    executor_version: str = Field(min_length=1, max_length=100)
    passed: bool
    error_code: str | None = Field(default=None, max_length=100)
    diagnostic: dict[str, Any] = Field(default_factory=dict)


class QuickTestResultReceiptPayload(BaseModel):
    session_id: UUID
    session_status: str
    receipt_id: UUID
    qualification_status: str
    completed_at: datetime


class CreateArtifactUploadPayload(BaseModel):
    owner_kind: str
    owner_id: UUID
    artifact_kind: str
    file_name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str


class ArtifactUploadInstructionsPayload(BaseModel):
    method: str
    url: str
    headers: dict[str, str]
    expires_at: datetime


class ArtifactUploadPayload(BaseModel):
    processing: bool = False
    artifact_id: UUID
    owner_kind: str
    owner_id: UUID
    artifact_kind: str
    file_name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str
    status: str
    expires_at: datetime
    completed_at: datetime | None
    blob_id: UUID | None
    row_version: int = Field(ge=1)
    upload: ArtifactUploadInstructionsPayload | None = None
