from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid5

from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.execution.content_store import ContentAddressedStore
from al1s_terminal.execution.resource_receiver import (
    AuthorizedResourceReceiver,
    ResourceManifestMismatchError,
)
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.transport.delivery import DeliveryPlatformPort
from al1s_terminal.transport.delivery_models import (
    TaskPackageBodyPayload,
    TerminalCommandPayload,
)
from al1s_terminal.types import (
    PackageManifestStatus,
    ReportKind,
    WorkItemKind,
    WorkItemStatus,
)

PACKAGE_RECEIPT_NAMESPACE = UUID("596f42c0-1b26-45f7-8768-d08c85f2b3b1")


class PackageValidationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ReceivedPackage:
    package_id: UUID
    work_item_id: UUID
    disposition: str
    report_id: UUID


class PackageReceiver:
    def __init__(
        self,
        *,
        platform: DeliveryPlatformPort,
        secret_store: FileSecretStore,
        content_store: ContentAddressedStore,
        uow_factory: Callable[[], LocalUnitOfWork],
        resource_receiver: AuthorizedResourceReceiver | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._platform = platform
        self._secret_store = secret_store
        self._content_store = content_store
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._resources = resource_receiver or AuthorizedResourceReceiver(
            platform=platform,
            content_store=content_store,
            uow_factory=uow_factory,
            clock=self._clock,
        )

    def receive(self, command: TerminalCommandPayload) -> ReceivedPackage:
        if command.kind != "task_package_available" or command.package_id is None:
            raise PackageValidationError(
                "COMMAND_KIND_UNSUPPORTED", "command does not contain a task package"
            )
        identity = self._secret_store.load()
        if identity is None:
            raise PackageValidationError(
                "TERMINAL_NOT_REGISTERED", "terminal identity is unavailable"
            )
        package = self._platform.get_task_package(
            identity.terminal_id,
            identity.credential,
            command.package_id,
        )
        body = TaskPackageBodyPayload.model_validate(package.body)
        if (
            package.package_id != body.package_id
            or package.attempt_id != body.attempt_id
            or package.execution_id != body.execution_id
        ):
            raise PackageValidationError(
                "PACKAGE_MANIFEST_INVALID",
                "task package envelope differs from its immutable body",
            )
        self._validate_envelope(
            identity.terminal_id,
            command,
            package.package_hash,
            package.body,
            body,
        )
        now = self._clock()
        with self._uow_factory() as uow:
            work_item = uow.work_items.add(
                kind=WorkItemKind.FORMAL_TASK,
                remote_id=package.package_id,
                content_hash=package.package_hash,
                target_device_id=body.target_device_id,
                available_at=(
                    body.queue_order.available_at if body.queue_order else command.available_at
                ),
                queue_enqueued_at=(body.queue_order.enqueued_at if body.queue_order else None),
                queue_order_id=(body.attempt_id if body.queue_order else None),
                payload={"command_id": str(command.command_id)},
                now=now,
            )
            manifest = uow.packages.add_receiving(
                package_id=package.package_id,
                work_item_id=work_item.work_item_id,
                attempt_id=body.attempt_id,
                execution_id=body.execution_id,
                package_hash=package.package_hash,
                protocol_version=body.protocol_version,
                package_schema_version=body.package_schema_version,
                snapshot_schema_version=body.snapshot_schema_version,
                manifest=package.body,
                now=now,
            )
        if (
            work_item.status is not WorkItemStatus.RECEIVING
            and manifest.status is PackageManifestStatus.READY
        ):
            return self._queue_receipt(command, work_item.work_item_id, "duplicate")

        for resource in body.resources:
            try:
                self._resources.receive(
                    identity.terminal_id,
                    identity.credential,
                    resource.blob_id,
                    expected_sha256=resource.sha256,
                    expected_size_bytes=resource.size,
                    expected_media_type=resource.media_type,
                    work_reference=(work_item.work_item_id, resource.resource_key, resource.role),
                )
            except ResourceManifestMismatchError as exc:
                raise PackageValidationError("PACKAGE_MANIFEST_INVALID", str(exc)) from exc

        relative_path = self._content_store.write_package(package.package_id, package.body)
        references = tuple(
            (item.resource_key, item.blob_id, item.sha256, item.role) for item in body.resources
        )
        with self._uow_factory() as uow:
            current = uow.work_items.get(work_item.work_item_id)
            if current is None:
                raise RuntimeError("package work item disappeared during receipt")
            uow.resources.add_work_item_references(work_item.work_item_id, references)
            uow.packages.mark_ready(
                package.package_id,
                relative_path=relative_path,
                now=self._clock(),
            )
            if current.status is WorkItemStatus.RECEIVING:
                uow.work_items.transition(
                    current.work_item_id,
                    expected=WorkItemStatus.RECEIVING,
                    target=WorkItemStatus.QUEUED,
                    now=self._clock(),
                    reason_code="package_persisted",
                )
        return self._queue_receipt(command, work_item.work_item_id, "accepted")

    def _queue_receipt(
        self,
        command: TerminalCommandPayload,
        work_item_id: UUID,
        disposition: str,
    ) -> ReceivedPackage:
        assert command.package_id is not None
        report_id = uuid5(PACKAGE_RECEIPT_NAMESPACE, str(command.command_id))
        payload = {
            "protocol_version": 1,
            "report_id": str(report_id),
            "command_id": str(command.command_id),
            "attempt_id": str(command.attempt_id),
            "disposition": disposition,
            "rejection_code": None,
            "diagnostic": None,
            "occurred_at": self._clock().isoformat(),
            "package_id": str(command.package_id),
        }
        with self._uow_factory() as uow:
            existing = uow.outbox.get(report_id)
            if existing is None:
                uow.outbox.enqueue(
                    report_id=report_id,
                    kind=ReportKind.PACKAGE_RECEIPT,
                    payload=payload,
                    work_item_id=work_item_id,
                    now=self._clock(),
                )
                persisted_disposition = disposition
            else:
                persisted_disposition = str(existing.payload["disposition"])
        return ReceivedPackage(
            command.package_id,
            work_item_id,
            persisted_disposition,
            report_id,
        )

    def queue_rejection(
        self,
        command: TerminalCommandPayload,
        *,
        rejection_code: str,
        diagnostic: str,
    ) -> UUID:
        if command.package_id is None:
            raise ValueError("package rejection requires a package id")
        report_id = uuid5(PACKAGE_RECEIPT_NAMESPACE, str(command.command_id))
        payload = {
            "protocol_version": 1,
            "report_id": str(report_id),
            "command_id": str(command.command_id),
            "attempt_id": str(command.attempt_id),
            "disposition": "rejected",
            "rejection_code": rejection_code,
            "diagnostic": diagnostic[:512],
            "occurred_at": self._clock().isoformat(),
            "package_id": str(command.package_id),
        }
        with self._uow_factory() as uow:
            existing = uow.outbox.get(report_id)
            if existing is None:
                uow.outbox.enqueue(
                    report_id=report_id,
                    kind=ReportKind.PACKAGE_RECEIPT,
                    payload=payload,
                    now=self._clock(),
                )
        return report_id

    @staticmethod
    def _validate_envelope(
        terminal_id: UUID,
        command: TerminalCommandPayload,
        package_hash: str,
        raw_body: dict[str, object],
        body: TaskPackageBodyPayload,
    ) -> None:
        canonical = json.dumps(
            raw_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        actual_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        announced_hash = command.payload.get("package_hash")
        if (
            body.protocol_version != 1
            or body.package_schema_version != 1
            or body.package_id != command.package_id
            or body.attempt_id != command.attempt_id
            or body.terminal_id != terminal_id
            or actual_hash != package_hash
            or announced_hash != package_hash
        ):
            raise PackageValidationError(
                "PACKAGE_MANIFEST_INVALID", "package identity or canonical hash is invalid"
            )
        keys = [item.resource_key for item in body.resources]
        if len(keys) != len(set(keys)):
            raise PackageValidationError(
                "PACKAGE_MANIFEST_INVALID", "package resource keys must be unique"
            )
