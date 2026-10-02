from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.execution.artifact_store import LocalArtifactStore
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.transport.delivery import DeliveryPlatformPort
from al1s_terminal.transport.delivery_models import (
    ArtifactUploadPayload,
    CreateArtifactUploadPayload,
)
from al1s_terminal.transport.platform import (
    PlatformCredentialRejectedError,
    PlatformError,
    PlatformUnavailableError,
)


class ArtifactUploadError(RuntimeError):
    pass


class ArtifactUploader:
    def __init__(
        self,
        *,
        platform: DeliveryPlatformPort,
        secret_store: FileSecretStore,
        data_dir: Path,
    ) -> None:
        self._platform = platform
        self._secret_store = secret_store
        self._data_dir = data_dir.resolve()

    def upload(
        self,
        *,
        path: Path,
        file_name: str | None = None,
        owner_kind: str,
        owner_id: UUID,
        artifact_kind: str,
        media_type: str,
        idempotency_key: str,
        expected_sha256: str | None = None,
        expected_size_bytes: int | None = None,
    ) -> ArtifactUploadPayload:
        identity = self._secret_store.load()
        if identity is None:
            raise ArtifactUploadError("terminal identity is unavailable")
        source = path.resolve(strict=True)
        if not source.is_relative_to(self._data_dir) or source.is_symlink() or not source.is_file():
            raise ArtifactUploadError("artifact path is outside the controlled data directory")
        size_bytes, sha256 = _file_identity(source)
        if expected_sha256 is not None and sha256 != expected_sha256:
            raise ArtifactUploadError("artifact content changed after it was staged")
        if expected_size_bytes is not None and size_bytes != expected_size_bytes:
            raise ArtifactUploadError("artifact size changed after it was staged")
        created = self._platform.create_artifact_upload(
            identity.terminal_id,
            identity.credential,
            CreateArtifactUploadPayload(
                owner_kind=owner_kind,
                owner_id=owner_id,
                artifact_kind=artifact_kind,
                file_name=file_name or source.name,
                sha256=sha256,
                size_bytes=size_bytes,
                media_type=media_type,
            ),
            idempotency_key=idempotency_key,
        )
        if created.status == "ready":
            return created
        if created.processing:
            raise PlatformUnavailableError(
                202, "artifact_processing", "Artifact verification is pending"
            )
        if created.upload is None or created.upload.method != "PUT":
            raise ArtifactUploadError("platform did not return PUT upload instructions")
        with source.open("rb") as body:
            self._platform.upload_artifact(
                created.upload.url,
                created.upload.headers,
                body,
            )
        completed = self._platform.complete_artifact_upload(
            identity.terminal_id,
            identity.credential,
            created.artifact_id,
        )
        if completed.processing:
            raise PlatformUnavailableError(
                202, "artifact_processing", "Artifact verification is pending"
            )
        return completed


class ArtifactUploadCoordinator:
    def __init__(
        self,
        *,
        uploader: ArtifactUploader,
        store: LocalArtifactStore,
        secret_store: FileSecretStore,
        uow_factory: Callable[[], LocalUnitOfWork],
        batch_size: int = 10,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 1 <= batch_size <= 20:
            raise ValueError("artifact upload batch size must be between 1 and 20")
        self._uploader = uploader
        self._store = store
        self._secret_store = secret_store
        self._uow_factory = uow_factory
        self._batch_size = batch_size
        self._clock = clock or (lambda: datetime.now(UTC))

    def flush(self) -> int:
        identity = self._secret_store.load()
        if identity is None:
            return 0
        worker_id = f"artifact-{identity.installation_id}"
        with self._uow_factory() as uow:
            artifacts = uow.artifacts.claim_batch(
                worker_id=worker_id,
                now=self._clock(),
                lease_duration=timedelta(minutes=5),
                limit=self._batch_size,
            )
        confirmed = 0
        for artifact in artifacts:
            try:
                completed = self._uploader.upload(
                    path=self._store.path_for(artifact.relative_path),
                    file_name=artifact.file_name,
                    owner_kind=artifact.owner_kind,
                    owner_id=artifact.owner_id,
                    artifact_kind=artifact.artifact_kind,
                    media_type=artifact.media_type,
                    idempotency_key=str(artifact.artifact_id),
                    expected_sha256=artifact.sha256,
                    expected_size_bytes=artifact.size_bytes,
                )
                if completed.status != "ready" or completed.blob_id is None:
                    raise ArtifactUploadError("platform did not confirm the uploaded artifact")
            except PlatformCredentialRejectedError:
                raise
            except (PlatformError, OSError) as exc:
                self._release(artifact.artifact_id, worker_id, type(exc).__name__)
                continue
            except (ArtifactUploadError, ValueError) as exc:
                self._release(
                    artifact.artifact_id,
                    worker_id,
                    type(exc).__name__,
                    permanent=True,
                )
                continue
            local_confirmed = False
            with self._uow_factory() as uow:
                local_confirmed = uow.artifacts.confirm(
                    artifact.artifact_id,
                    worker_id=worker_id,
                    platform_artifact_id=completed.artifact_id,
                    platform_blob_id=completed.blob_id,
                    now=self._clock(),
                )
            if local_confirmed:
                confirmed += 1
        return confirmed

    def _release(
        self,
        artifact_id: UUID,
        worker_id: str,
        error_code: str,
        *,
        permanent: bool = False,
    ) -> None:
        with self._uow_factory() as uow:
            uow.artifacts.release_failed(
                artifact_id,
                worker_id=worker_id,
                available_at=self._clock() + timedelta(seconds=30),
                error_code=error_code,
                permanent=permanent,
            )


def _file_identity(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()
