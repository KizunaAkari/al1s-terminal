from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine

from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.execution.content_store import ContentAddressedStore
from al1s_terminal.execution.quick_test_receiver import (
    QuickTestReceiver,
    QuickTestValidationError,
)
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.transport.delivery_models import QuickTestClaimPayload
from al1s_terminal.types import SecureIdentity, WorkItemKind, WorkItemStatus

NOW = datetime(2026, 8, 31, 19, 0, tzinfo=UTC)


class NoResourceReceiver:
    def receive(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("resource download was not expected")


def _claim(*, valid_hash: bool = True) -> QuickTestClaimPayload:
    manifest = {"definition_type": "quick_test", "steps": [{"action": "wait"}]}
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
    definition_hash = hashlib.sha256(canonical.encode()).hexdigest()
    if not valid_hash:
        definition_hash = "0" * 64
    return QuickTestClaimPayload.model_validate(
        {
            "session": {
                "session_id": str(uuid4()),
                "script_id": str(uuid4()),
                "candidate_version_id": str(uuid4()),
                "candidate_manifest_hash": "1" * 64,
                "definition_hash": definition_hash,
                "target_device_id": str(uuid4()),
                "status": "claimed",
                "expires_at": (NOW + timedelta(minutes=15)).isoformat(),
                "row_version": 2,
            },
            "definition": {
                "revision_id": f"candidate-version:{uuid4()}",
                "schema_version": 1,
                "manifest_hash": definition_hash,
                "manifest": manifest,
                "capability_requirements": {
                    "provider_keys": ["maa"],
                    "requires_target_device": True,
                },
                "blobs": [],
            },
        }
    )


def _receiver(local_engine: Engine, tmp_path: Path) -> QuickTestReceiver:
    secret_store = FileSecretStore(tmp_path / "secrets" / "terminal-credential")
    secret_store.save(SecureIdentity(uuid4(), uuid4(), "credential", 1, 1))
    return QuickTestReceiver(
        secret_store=secret_store,
        content_store=ContentAddressedStore(tmp_path),
        resource_receiver=NoResourceReceiver(),  # type: ignore[arg-type]
        uow_factory=lambda: LocalUnitOfWork.from_engine(local_engine),
        clock=lambda: NOW,
    )


def test_claimed_quick_test_is_durably_queued_and_replay_is_idempotent(
    local_engine: Engine, tmp_path: Path
) -> None:
    receiver = _receiver(local_engine, tmp_path)
    claim = _claim()

    first = receiver.receive(claim)
    replay = receiver.receive(claim)

    assert first.work_item_id == replay.work_item_id
    assert replay.status is WorkItemStatus.QUEUED
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        stored = uow.work_items.get_by_remote(WorkItemKind.QUICK_TEST, claim.session.session_id)
        assert stored is not None
        assert stored.status is WorkItemStatus.QUEUED
    assert (tmp_path / "quick-tests" / f"{claim.session.session_id}.json").is_file()


def test_claimed_quick_test_replay_is_idempotent_while_running(
    local_engine: Engine, tmp_path: Path
) -> None:
    receiver = _receiver(local_engine, tmp_path)
    claim = _claim()
    first = receiver.receive(claim)
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        uow.work_items.mark_running(
            first.work_item_id,
            lease_id=None,
            lease_version=None,
            now=NOW,
        )

    replay = receiver.receive(claim)

    assert replay.work_item_id == first.work_item_id
    assert replay.status is WorkItemStatus.RUNNING


def test_quick_test_rejects_definition_hash_mismatch(local_engine: Engine, tmp_path: Path) -> None:
    with pytest.raises(QuickTestValidationError, match="definition hash"):
        _receiver(local_engine, tmp_path).receive(_claim(valid_hash=False))
