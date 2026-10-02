from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.execution.content_store import ContentAddressedStore
from al1s_terminal.execution.package_receiver import PackageReceiver
from al1s_terminal.persistence.models import InboxWorkItemRow, WorkItemResourceRow
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.transport.delivery_models import (
    TaskPackagePayload,
    TerminalBlobMetadataPayload,
    TerminalCommandPayload,
)
from al1s_terminal.types import (
    OutboxStatus,
    PackageManifestStatus,
    SecureIdentity,
    WorkItemKind,
    WorkItemStatus,
)

NOW = datetime(2026, 8, 31, 16, 0, tzinfo=UTC)


def test_package_pins_resources_before_downloading(local_engine, tmp_path, monkeypatch):
    receiver, platform, command, _ = _receiver(local_engine, tmp_path)
    download = platform.download_blob_range
    observed = []

    def checked_download(*args, **kwargs):
        with Session(local_engine) as session:
            pin = session.scalar(select(WorkItemResourceRow))
            assert pin is not None
            observed.append(pin.sha256)
        return download(*args, **kwargs)

    monkeypatch.setattr(platform, "download_blob_range", checked_download)
    receiver.receive(command)
    assert observed


class FakeDeliveryPlatform:
    def __init__(self, package: TaskPackagePayload, resource: bytes) -> None:
        self.package = package
        self.resource = resource
        self.download_count = 0

    def get_task_package(
        self, terminal_id: UUID, credential: str, package_id: UUID
    ) -> TaskPackagePayload:
        assert terminal_id == UUID(str(self.package.body["terminal_id"]))
        assert credential == "credential"
        assert package_id == self.package.package_id
        return self.package

    def head_blob(
        self, terminal_id: UUID, credential: str, blob_id: UUID
    ) -> TerminalBlobMetadataPayload:
        item = self.package.body["resources"][0]
        assert isinstance(item, dict)
        assert blob_id == UUID(str(item["blob_id"]))
        return TerminalBlobMetadataPayload(
            blob_id=blob_id,
            sha256=str(item["sha256"]),
            size_bytes=int(item["size"]),
            media_type=str(item["media_type"]),
        )

    def download_blob_range(
        self,
        terminal_id: UUID,
        credential: str,
        blob_id: UUID,
        *,
        start: int,
        end_inclusive: int,
    ) -> bytes:
        self.download_count += 1
        return self.resource[start : end_inclusive + 1]


def _receiver(
    local_engine: Engine, tmp_path: Path
) -> tuple[PackageReceiver, FakeDeliveryPlatform, TerminalCommandPayload, UUID]:
    terminal_id = uuid4()
    package_id = uuid4()
    attempt_id = uuid4()
    execution_id = uuid4()
    blob_id = uuid4()
    resource = b"maa-template"
    resource_hash = hashlib.sha256(resource).hexdigest()
    body: dict[str, object] = {
        "protocol_version": 1,
        "package_schema_version": 1,
        "snapshot_schema_version": 1,
        "package_id": str(package_id),
        "execution_id": str(execution_id),
        "attempt_id": str(attempt_id),
        "snapshot_id": str(uuid4()),
        "terminal_id": str(terminal_id),
        "target_device_id": str(uuid4()),
        "created_at": NOW.isoformat(),
        "source": {"module": "maa", "logical_content_id": "script:test"},
        "manifest": {"entry": "main"},
        "resources": [
            {
                "blob_id": str(blob_id),
                "resource_key": "template:1",
                "role": "template",
                "sha256": resource_hash,
                "size": len(resource),
                "media_type": "image/png",
            }
        ],
        "parameters": {},
        "capability_requirements": {"provider_keys": ["maa"]},
        "timeout_seconds": 60,
        "attempt_no": 1,
        "max_retries": 0,
        "record_video": False,
    }
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    package_hash = hashlib.sha256(canonical.encode()).hexdigest()
    package = TaskPackagePayload(
        package_id=package_id,
        attempt_id=attempt_id,
        execution_id=execution_id,
        package_hash=package_hash,
        status="available",
        body=body,
        row_version=1,
    )
    command = TerminalCommandPayload(
        command_id=uuid4(),
        kind="task_package_available",
        package_id=package_id,
        attempt_id=attempt_id,
        delivery_no=1,
        status="pending",
        payload={"package_hash": package_hash},
        available_at=NOW,
        created_at=NOW,
        row_version=1,
    )
    secret_store = FileSecretStore(tmp_path / "secrets" / "terminal-credential")
    secret_store.save(SecureIdentity(uuid4(), terminal_id, "credential", 1, 1))
    platform = FakeDeliveryPlatform(package, resource)
    return (
        PackageReceiver(
            platform=platform,  # type: ignore[arg-type]
            secret_store=secret_store,
            content_store=ContentAddressedStore(tmp_path),
            uow_factory=lambda: LocalUnitOfWork.from_engine(local_engine),
            clock=lambda: NOW,
        ),
        platform,
        command,
        blob_id,
    )


def test_package_is_queued_only_after_resources_and_manifest_are_durable(
    local_engine: Engine, tmp_path: Path
) -> None:
    receiver, platform, command, _blob_id = _receiver(local_engine, tmp_path)

    first = receiver.receive(command)
    replay = receiver.receive(command)

    assert first.disposition == "accepted"
    assert replay.disposition == "accepted"
    assert platform.download_count == 1
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        work_item = uow.work_items.get_by_remote(WorkItemKind.FORMAL_TASK, first.package_id)
        assert work_item is not None
        assert work_item.status is WorkItemStatus.QUEUED
        package = uow.packages.get(first.package_id)
        assert package is not None
        assert package.status is PackageManifestStatus.READY
        report = uow.outbox.get(first.report_id)
        assert report is not None
        assert report.status is OutboxStatus.PENDING
    assert list((tmp_path / "packages").glob("*.json"))


def test_package_projects_hashed_platform_fifo_into_typed_columns(
    local_engine: Engine, tmp_path: Path
) -> None:
    receiver, platform, command, _ = _receiver(local_engine, tmp_path)
    enqueue_time = NOW - timedelta(minutes=5)
    platform.package.body["queue_order"] = {
        "available_at": enqueue_time.isoformat(),
        "enqueued_at": enqueue_time.isoformat(),
    }
    package_hash = hashlib.sha256(
        json.dumps(
            platform.package.body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    platform.package.package_hash = package_hash
    command.payload["package_hash"] = package_hash
    received = receiver.receive(command)
    with Session(local_engine) as session:
        row = session.get(InboxWorkItemRow, str(received.work_item_id))
        assert row is not None
        assert row.queue_order_id == str(platform.package.attempt_id)
        assert row.queue_enqueued_at.replace(tzinfo=UTC) == enqueue_time
        assert row.available_at.replace(tzinfo=UTC) == enqueue_time
        assert row.created_at.replace(tzinfo=UTC) == NOW


def test_new_delivery_command_for_ready_package_returns_duplicate(
    local_engine: Engine, tmp_path: Path
) -> None:
    receiver, _platform, command, _blob_id = _receiver(local_engine, tmp_path)
    receiver.receive(command)
    redelivery = command.model_copy(
        update={"command_id": uuid4(), "delivery_no": command.delivery_no + 1}
    )

    result = receiver.receive(redelivery)

    assert result.disposition == "duplicate"


def test_ready_package_redelivery_is_idempotent_while_running(
    local_engine: Engine, tmp_path: Path
) -> None:
    receiver, platform, command, _blob_id = _receiver(local_engine, tmp_path)
    first = receiver.receive(command)
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        uow.work_items.mark_running(
            first.work_item_id,
            lease_id=uuid4(),
            lease_version=1,
            now=NOW,
        )
    redelivery = command.model_copy(
        update={"command_id": uuid4(), "delivery_no": command.delivery_no + 1}
    )

    result = receiver.receive(redelivery)

    assert result.disposition == "duplicate"
    assert platform.download_count == 1
