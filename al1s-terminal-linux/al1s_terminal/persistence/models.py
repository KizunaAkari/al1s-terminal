from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from al1s_terminal.persistence.base import Base


class TerminalInstallationRow(Base):
    __tablename__ = "terminal_installation"
    __table_args__ = (
        CheckConstraint("singleton_id = 1", name="ck_terminal_installation_singleton"),
        CheckConstraint(
            "registration_status IN ('unregistered', 'registered', 'credential_rejected')",
            name="ck_terminal_installation_status",
        ),
        CheckConstraint("credential_epoch >= 0", name="ck_terminal_installation_epoch"),
        CheckConstraint("capability_revision >= 0", name="ck_terminal_capability_revision"),
        CheckConstraint("row_version > 0", name="ck_terminal_installation_version"),
    )

    singleton_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    installation_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    terminal_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    registration_status: Mapped[str] = mapped_column(String(32), nullable=False)
    terminal_row_version: Mapped[int | None] = mapped_column(Integer)
    credential_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_capability_hash: Mapped[str | None] = mapped_column(String(64))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InboxWorkItemRow(Base):
    __tablename__ = "inbox_work_items"
    __table_args__ = (
        UniqueConstraint("kind", "remote_id", name="uq_inbox_kind_remote"),
        CheckConstraint("kind IN ('formal_task', 'quick_test')", name="ck_inbox_kind"),
        CheckConstraint(
            "status IN ('receiving', 'queued', 'running', 'result_pending', "
            "'completed', 'cancelled', 'interrupted', 'rejected')",
            name="ck_inbox_status",
        ),
        CheckConstraint(
            "cancel_outcome IS NULL OR cancel_outcome IN "
            "('running_cancel_accepted', 'already_completed', 'cancelled_before_start')",
            name="ck_inbox_cancel_outcome",
        ),
        CheckConstraint(
            "(cancel_command_id IS NULL AND cancel_requested_at IS NULL "
            "AND cancel_reason IS NULL AND cancel_outcome IS NULL) OR "
            "(cancel_command_id IS NOT NULL AND cancel_requested_at IS NOT NULL "
            "AND cancel_reason IS NOT NULL AND cancel_outcome IS NOT NULL)",
            name="ck_inbox_cancel_fact",
        ),
        CheckConstraint("row_version > 0", name="ck_inbox_version"),
        Index("ix_inbox_runnable", "status", "available_at", "created_at", "id"),
        Index(
            "ix_inbox_platform_fifo",
            "status",
            "available_at",
            "queue_enqueued_at",
            "queue_order_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    remote_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target_device_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    queue_enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    queue_order_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    lease_id: Mapped[str | None] = mapped_column(String(36))
    lease_version: Mapped[int | None] = mapped_column(Integer)
    cancel_command_id: Mapped[str | None] = mapped_column(String(36))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(String(32))
    cancel_outcome: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class QuickTestEventRow(Base):
    __tablename__ = "quick_test_events"
    __table_args__ = (
        CheckConstraint("sequence BETWEEN 1 AND 1000", name="ck_local_quick_event_seq"),
        CheckConstraint(
            "kind IN ('started','step_started','step_succeeded','step_failed','log')",
            name="ck_local_quick_event_kind",
        ),
        Index(
            "ix_local_quick_event_pending", "confirmed_at", "created_at",
            "session_id", "sequence",
        ),
    )

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("inbox_work_items.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    step_number: Mapped[int | None] = mapped_column(Integer)
    code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OutboxReportRow(Base):
    __tablename__ = "outbox_reports"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_outbox_attempt_count"),
        CheckConstraint(
            "kind IN ('package_receipt', 'attempt_start', 'attempt_result', 'quick_test_result')",
            name="ck_outbox_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'confirmed', 'dead_letter')",
            name="ck_outbox_status",
        ),
        CheckConstraint("row_version > 0", name="ck_outbox_version"),
        Index("ix_outbox_available", "status", "available_at", "created_at", "report_id"),
        Index("ix_outbox_claim", "status", "claimed_until"),
    )

    report_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    work_item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("inbox_work_items.id", ondelete="RESTRICT")
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LocalTransitionRow(Base):
    __tablename__ = "local_transitions"
    __table_args__ = (Index("ix_local_transition_work_item", "work_item_id", "created_at", "id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    work_item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("inbox_work_items.id", ondelete="RESTRICT"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PackageManifestRow(Base):
    __tablename__ = "package_manifests"
    __table_args__ = (
        CheckConstraint("protocol_version > 0", name="ck_package_manifests_protocol"),
        CheckConstraint("package_schema_version > 0", name="ck_package_manifests_schema"),
        CheckConstraint("snapshot_schema_version > 0", name="ck_package_snapshot_schema"),
        CheckConstraint(
            "status IN ('receiving', 'ready', 'rejected')",
            name="ck_package_manifests_status",
        ),
        CheckConstraint("row_version > 0", name="ck_package_manifests_version"),
        Index("ix_package_manifests_status", "status", "created_at", "package_id"),
    )

    package_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    work_item_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("inbox_work_items.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    execution_id: Mapped[str] = mapped_column(String(36), nullable=False)
    package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    protocol_version: Mapped[int] = mapped_column(Integer, nullable=False)
    package_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    relative_path: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class CachedResourceRow(Base):
    __tablename__ = "cached_resources"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_cached_resources_size"),
        CheckConstraint(
            "status IN ('downloading', 'ready', 'quarantined')",
            name="ck_cached_resources_status",
        ),
        CheckConstraint("row_version > 0", name="ck_cached_resources_version"),
        Index("ix_cached_resources_status", "status", "updated_at", "sha256"),
    )

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    blob_id: Mapped[str] = mapped_column(String(36), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class WorkItemResourceRow(Base):
    __tablename__ = "work_item_resources"
    __table_args__ = (
        UniqueConstraint(
            "work_item_id", "blob_id", "role", name="uq_work_item_resources_blob_role"
        ),
        Index("ix_work_item_resources_sha", "sha256", "work_item_id"),
    )

    work_item_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("inbox_work_items.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    resource_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    blob_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sha256: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("cached_resources.sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(80), nullable=False)


class TargetDeviceObservationRow(Base):
    __tablename__ = "target_device_observations"
    __table_args__ = (
        CheckConstraint(
            "adb_state IN ('device', 'offline', 'unauthorized', 'other')",
            name="ck_target_device_observations_state",
        ),
        CheckConstraint("identifier_row_version > 0", name="ck_target_identifier_version"),
        CheckConstraint("row_version > 0", name="ck_target_device_observations_version"),
        Index(
            "ix_target_device_observations_target",
            "target_device_id",
            "adb_state",
            "last_seen_at",
        ),
    )

    adb_serial: Mapped[str] = mapped_column(String(512), primary_key=True)
    identifier_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    target_device_id: Mapped[str | None] = mapped_column(String(36))
    identifier_row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    adb_state: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(255))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LocalArtifactRow(Base):
    __tablename__ = "local_artifacts"
    __table_args__ = (
        UniqueConstraint("work_item_id", "relative_path", name="uq_local_artifact_path"),
        CheckConstraint(
            "owner_kind IN ('formal_attempt', 'quick_test')",
            name="ck_local_artifacts_owner_kind",
        ),
        CheckConstraint(
            "artifact_kind IN ('screenshot', 'video', 'log', 'diagnostic')",
            name="ck_local_artifacts_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'confirmed', 'dead_letter')",
            name="ck_local_artifacts_status",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_local_artifacts_size"),
        CheckConstraint("attempt_count >= 0", name="ck_local_artifacts_attempt_count"),
        CheckConstraint("row_version > 0", name="ck_local_artifacts_version"),
        Index(
            "ix_local_artifacts_available",
            "status",
            "available_at",
            "created_at",
            "artifact_id",
        ),
        Index("ix_local_artifacts_claim", "status", "claimed_until"),
        Index("ix_local_artifacts_work", "work_item_id", "created_at", "artifact_id"),
    )

    artifact_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    work_item_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("inbox_work_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    artifact_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    platform_artifact_id: Mapped[str | None] = mapped_column(String(36))
    platform_blob_id: Mapped[str | None] = mapped_column(String(36))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LocalGcJobRow(Base):
    __tablename__ = "local_gc_jobs"
    __table_args__ = (
        UniqueConstraint("target_kind", "target_id", name="uq_local_gc_target"),
        CheckConstraint(
            "target_kind IN ('artifact', 'work_item', 'resource')",
            name="ck_local_gc_target_kind",
        ),
        CheckConstraint(
            "status IN ('pending', 'claimed', 'dead_letter')",
            name="ck_local_gc_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_local_gc_attempt_count"),
        CheckConstraint("row_version > 0", name="ck_local_gc_version"),
        Index("ix_local_gc_available", "status", "available_at", "created_at", "job_id"),
        Index("ix_local_gc_claim", "status", "claimed_until"),
    )

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    relative_path: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class OfflineStartPermitRow(Base):
    __tablename__ = "offline_start_permits"
    __table_args__ = (
        UniqueConstraint("work_item_id", name="uq_offline_start_permits_work_item"),
        UniqueConstraint("attempt_id", name="uq_offline_start_permits_attempt"),
        CheckConstraint("permit_version > 0", name="ck_offline_start_permits_protocol"),
        CheckConstraint("row_version > 0", name="ck_offline_start_permits_version"),
        CheckConstraint(
            "status IN ('issued', 'consumed', 'revoked', 'expired')",
            name="ck_offline_start_permits_status",
        ),
        CheckConstraint("expires_at > issued_at", name="ck_offline_start_permits_expiry"),
        CheckConstraint(
            "(status = 'issued' AND consumed_at IS NULL "
            "AND consumed_start_report_id IS NULL AND revoked_at IS NULL) OR "
            "(status = 'consumed' AND consumed_at IS NOT NULL "
            "AND consumed_start_report_id IS NOT NULL AND revoked_at IS NULL) OR "
            "(status IN ('revoked', 'expired') AND consumed_at IS NULL "
            "AND consumed_start_report_id IS NULL AND revoked_at IS NOT NULL)",
            name="ck_offline_start_permits_lifecycle",
        ),
        Index("ix_offline_start_permits_status", "status", "expires_at", "permit_id"),
    )

    permit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    work_item_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("inbox_work_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    package_id: Mapped[str] = mapped_column(String(36), nullable=False)
    package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    permit_version: Mapped[int] = mapped_column(Integer, nullable=False)
    token: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_start_report_id: Mapped[str | None] = mapped_column(String(36))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
