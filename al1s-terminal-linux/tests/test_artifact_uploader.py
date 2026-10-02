from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine

from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.execution.artifact_store import LocalArtifactStore
from al1s_terminal.execution.artifact_uploader import (
    ArtifactUploadCoordinator,
    ArtifactUploader,
)
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.transport.delivery_models import (
    ArtifactUploadInstructionsPayload,
    ArtifactUploadPayload,
    CreateArtifactUploadPayload,
)
from al1s_terminal.types import LocalArtifactStatus, SecureIdentity, WorkItemKind

NOW = datetime(2026, 8, 31, 21, 0, tzinfo=UTC)


class FakeArtifactPlatform:
    def __init__(self) -> None:
        self.artifact_id = uuid4()
        self.created_payload: CreateArtifactUploadPayload | None = None
        self.uploaded = b""

    def create_artifact_upload(
        self,
        terminal_id: UUID,
        credential: str,
        payload: CreateArtifactUploadPayload,
        *,
        idempotency_key: str,
    ) -> ArtifactUploadPayload:
        self.created_payload = payload
        return _artifact(
            self.artifact_id,
            payload,
            status="pending",
            upload=ArtifactUploadInstructionsPayload(
                method="PUT",
                url="https://objects.test/upload",
                headers={"Content-Type": payload.media_type},
                expires_at=NOW + timedelta(minutes=15),
            ),
        )

    def upload_artifact(self, url: str, headers: dict[str, str], body: BinaryIO) -> None:
        self.uploaded = body.read()

    def complete_artifact_upload(
        self, terminal_id: UUID, credential: str, artifact_id: UUID
    ) -> ArtifactUploadPayload:
        assert self.created_payload is not None
        return _artifact(
            artifact_id,
            self.created_payload,
            status="ready",
            upload=None,
        )


@pytest.mark.parametrize("during_create", [True, False])
def test_async_completion_is_retryable_not_permanent(tmp_path, monkeypatch, during_create):
    from al1s_terminal.transport.platform import PlatformUnavailableError

    source = tmp_path / "image.png"
    source.write_bytes(b"image")
    secrets = FileSecretStore(tmp_path / "credential")
    secrets.save(SecureIdentity(uuid4(), uuid4(), "credential", 1, 1))
    platform = FakeArtifactPlatform()
    method = "create_artifact_upload" if during_create else "complete_artifact_upload"
    original = getattr(platform, method)

    def pending(*args, **kwargs):
        return original(*args, **kwargs).model_copy(
            update={"status": "pending", "processing": True, "upload": None}
        )

    monkeypatch.setattr(platform, method, pending)
    uploader = ArtifactUploader(platform=platform, secret_store=secrets, data_dir=tmp_path)
    with pytest.raises(PlatformUnavailableError):
        uploader.upload(
            path=source,
            owner_kind="formal_attempt",
            owner_id=uuid4(),
            artifact_kind="screenshot",
            media_type="image/png",
            idempotency_key="test",
        )
    assert source.exists()
    assert platform.uploaded == (b"" if during_create else b"image")


def test_artifact_uploader_hashes_streams_and_completes_controlled_file(
    tmp_path: Path,
) -> None:
    source = tmp_path / "artifacts" / "failure.png"
    source.parent.mkdir()
    source.write_bytes(b"image")
    secrets = FileSecretStore(tmp_path / "secrets" / "terminal-credential")
    secrets.save(SecureIdentity(uuid4(), uuid4(), "credential", 1, 1))
    platform = FakeArtifactPlatform()
    uploader = ArtifactUploader(
        platform=platform,  # type: ignore[arg-type]
        secret_store=secrets,
        data_dir=tmp_path,
    )

    completed = uploader.upload(
        path=source,
        owner_kind="formal_attempt",
        owner_id=uuid4(),
        artifact_kind="screenshot",
        media_type="image/png",
        idempotency_key="screenshot-1",
    )

    assert completed.status == "ready"
    assert platform.uploaded == b"image"
    assert platform.created_payload is not None
    assert platform.created_payload.size_bytes == 5
    assert platform.created_payload.sha256 == (
        "6105d6cc76af400325e94d588ce511be5bfdbb73b437dc51eca43917d7a43e3d"
    )


def test_artifact_upload_coordinator_preserves_business_name_and_confirms_queue(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    source = tmp_path / "runtime" / "evidence.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"evidence")
    terminal_id = uuid4()
    secrets = FileSecretStore(tmp_path / "secrets" / "terminal-credential")
    secrets.save(SecureIdentity(uuid4(), terminal_id, "credential", 1, 1))
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        work = uow.work_items.add(
            kind=WorkItemKind.FORMAL_TASK,
            remote_id=uuid4(),
            content_hash="a" * 64,
            target_device_id=uuid4(),
            available_at=NOW,
            payload={},
            now=NOW,
        )
    artifact_id = uuid4()
    store = LocalArtifactStore(tmp_path)
    staged = store.stage(
        artifact_id=artifact_id,
        work_item_id=work.work_item_id,
        owner_kind="formal_attempt",
        owner_id=uuid4(),
        artifact_kind="screenshot",
        file_name="example-script-step-02.png",
        media_type="image/png",
        source=source,
    )
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        uow.artifacts.add_many((staged,), now=NOW)
    platform = FakeArtifactPlatform()
    coordinator = ArtifactUploadCoordinator(
        uploader=ArtifactUploader(
            platform=platform,  # type: ignore[arg-type]
            secret_store=secrets,
            data_dir=tmp_path,
        ),
        store=store,
        secret_store=secrets,
        uow_factory=lambda: LocalUnitOfWork.from_engine(local_engine),
        clock=lambda: NOW,
    )

    assert coordinator.flush() == 1

    assert platform.created_payload is not None
    assert platform.created_payload.file_name == "example-script-step-02.png"
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        artifact = uow.artifacts.get(artifact_id)
    assert artifact is not None
    assert artifact.status is LocalArtifactStatus.CONFIRMED
    assert artifact.platform_artifact_id == platform.artifact_id
    assert artifact.platform_blob_id is not None
    assert (tmp_path / staged.relative_path).exists()


def _artifact(
    artifact_id: UUID,
    request: CreateArtifactUploadPayload,
    *,
    status: str,
    upload: ArtifactUploadInstructionsPayload | None,
) -> ArtifactUploadPayload:
    return ArtifactUploadPayload(
        artifact_id=artifact_id,
        owner_kind=request.owner_kind,
        owner_id=request.owner_id,
        artifact_kind=request.artifact_kind,
        file_name=request.file_name,
        sha256=request.sha256,
        size_bytes=request.size_bytes,
        media_type=request.media_type,
        status=status,
        expires_at=NOW + timedelta(minutes=15),
        completed_at=NOW if status == "ready" else None,
        blob_id=uuid4() if status == "ready" else None,
        row_version=2 if status == "ready" else 1,
        upload=upload,
    )
