from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.execution.content_store import ContentAddressedStore
from al1s_terminal.execution.resource_receiver import AuthorizedResourceReceiver
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.transport.delivery_models import QuickTestClaimPayload
from al1s_terminal.types import WorkItemKind, WorkItemRecord, WorkItemStatus


class QuickTestValidationError(RuntimeError):
    pass


class QuickTestReceiver:
    def __init__(
        self,
        *,
        secret_store: FileSecretStore,
        content_store: ContentAddressedStore,
        resource_receiver: AuthorizedResourceReceiver,
        uow_factory: Callable[[], LocalUnitOfWork],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._secret_store = secret_store
        self._content_store = content_store
        self._resources = resource_receiver
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def receive(self, claimed: QuickTestClaimPayload) -> WorkItemRecord:
        identity = self._secret_store.load()
        if identity is None:
            raise QuickTestValidationError("terminal identity is unavailable")
        session = claimed.session
        definition = claimed.definition
        if session.status != "claimed":
            raise QuickTestValidationError("quick test was not claimed by the terminal")
        canonical = json.dumps(
            definition.manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if hashlib.sha256(canonical.encode("utf-8")).hexdigest() != definition.manifest_hash:
            raise QuickTestValidationError("quick-test definition hash is invalid")
        if session.definition_hash != definition.manifest_hash:
            raise QuickTestValidationError("quick-test session and definition hashes differ")
        keys = [item.resource_key for item in definition.blobs]
        if len(keys) != len(set(keys)):
            raise QuickTestValidationError("quick-test resource keys must be unique")

        now = self._clock()
        relative_path = f"quick-tests/{session.session_id}.json"
        with self._uow_factory() as uow:
            work_item = uow.work_items.add(
                kind=WorkItemKind.QUICK_TEST,
                remote_id=session.session_id,
                content_hash=definition.manifest_hash,
                target_device_id=session.target_device_id,
                available_at=now,
                payload={
                    "script_id": str(session.script_id),
                    "candidate_version_id": str(session.candidate_version_id),
                    "candidate_manifest_hash": session.candidate_manifest_hash,
                    "definition_path": relative_path,
                    "expires_at": session.expires_at.isoformat(),
                },
                now=now,
            )
        # The platform can redeliver the same claimed session until its result
        # is acknowledged. Once local receipt has moved beyond RECEIVING, the
        # immutable content is already durable and redelivery is a no-op.
        if work_item.status is not WorkItemStatus.RECEIVING:
            return work_item

        references: list[tuple[str, UUID, str, str]] = []
        for item in definition.blobs:
            resource = self._resources.receive(
                identity.terminal_id,
                identity.credential,
                item.blob_id,
                work_reference=(work_item.work_item_id, item.resource_key, item.role),
            )
            references.append((item.resource_key, item.blob_id, resource.sha256, item.role))
        self._content_store.write_quick_test(
            session.session_id,
            claimed.model_dump(mode="json"),
        )
        with self._uow_factory() as uow:
            current = uow.work_items.get(work_item.work_item_id)
            if current is None:
                raise RuntimeError("quick-test work item disappeared during receipt")
            uow.resources.add_work_item_references(work_item.work_item_id, tuple(references))
            if current.status is WorkItemStatus.RECEIVING:
                return uow.work_items.transition(
                    current.work_item_id,
                    expected=WorkItemStatus.RECEIVING,
                    target=WorkItemStatus.QUEUED,
                    now=self._clock(),
                    reason_code="quick_test_persisted",
                )
            return current
