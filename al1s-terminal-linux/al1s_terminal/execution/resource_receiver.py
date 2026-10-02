from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from al1s_terminal.execution.content_store import ContentAddressedStore
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.transport.delivery import DeliveryPlatformPort


class ResourceManifestMismatchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReceivedResource:
    blob_id: UUID
    sha256: str
    size_bytes: int
    media_type: str


class AuthorizedResourceReceiver:
    def __init__(
        self,
        *,
        platform: DeliveryPlatformPort,
        content_store: ContentAddressedStore,
        uow_factory: Callable[[], LocalUnitOfWork],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._platform = platform
        self._content_store = content_store
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def receive(
        self,
        terminal_id: UUID,
        credential: str,
        blob_id: UUID,
        *,
        expected_sha256: str | None = None,
        expected_size_bytes: int | None = None,
        expected_media_type: str | None = None,
        work_reference: tuple[UUID, str, str] | None = None,
    ) -> ReceivedResource:
        metadata = self._platform.head_blob(terminal_id, credential, blob_id)
        if (
            (expected_sha256 is not None and metadata.sha256 != expected_sha256)
            or (expected_size_bytes is not None and metadata.size_bytes != expected_size_bytes)
            or (
                expected_media_type is not None
                and _base_media_type(metadata.media_type) != _base_media_type(expected_media_type)
            )
        ):
            raise ResourceManifestMismatchError(
                "authorized Blob metadata differs from the immutable manifest"
            )
        relative_path = self._content_store.blob_relative_path(metadata.sha256)
        with self._uow_factory() as uow:
            uow.resources.ensure_downloading(
                sha256=metadata.sha256,
                blob_id=metadata.blob_id,
                size_bytes=metadata.size_bytes,
                media_type=metadata.media_type,
                relative_path=relative_path,
                now=self._clock(),
            )
            if work_reference is not None:
                work_id, resource_key, role = work_reference
                uow.resources.add_work_item_references(
                    work_id, ((resource_key, blob_id, metadata.sha256, role),)
                )
        self._content_store.ensure_blob(
            sha256=metadata.sha256,
            size_bytes=metadata.size_bytes,
            download_range=lambda start, end: self._platform.download_blob_range(
                terminal_id,
                credential,
                metadata.blob_id,
                start=start,
                end_inclusive=end,
            ),
        )
        with self._uow_factory() as uow:
            uow.resources.mark_ready(metadata.sha256, now=self._clock())
        return ReceivedResource(
            metadata.blob_id,
            metadata.sha256,
            metadata.size_bytes,
            metadata.media_type,
        )


def _base_media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()
