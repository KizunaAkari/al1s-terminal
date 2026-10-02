from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, func, select

from al1s_terminal.persistence.models import LocalTransitionRow
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.types import (
    LocalArtifactStatus,
    NewLocalArtifact,
    OutboxStatus,
    ReportKind,
    WorkItemKind,
    WorkItemStatus,
)

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _uow(engine: Engine) -> LocalUnitOfWork:
    return LocalUnitOfWork.from_engine(engine)


def test_work_item_is_idempotent_and_rejects_same_id_different_hash(
    local_engine: Engine,
) -> None:
    remote_id = uuid4()
    with _uow(local_engine) as uow:
        first = uow.work_items.add(
            kind=WorkItemKind.FORMAL_TASK,
            remote_id=remote_id,
            content_hash="a" * 64,
            target_device_id=None,
            available_at=NOW,
            payload={"package_id": str(remote_id)},
            now=NOW,
        )
    with _uow(local_engine) as uow:
        repeated = uow.work_items.add(
            kind=WorkItemKind.FORMAL_TASK,
            remote_id=remote_id,
            content_hash="a" * 64,
            target_device_id=None,
            available_at=NOW,
            payload={"ignored_on_replay": True},
            now=NOW,
        )
        assert repeated.work_item_id == first.work_item_id
        with pytest.raises(ValueError, match="different content"):
            uow.work_items.add(
                kind=WorkItemKind.FORMAL_TASK,
                remote_id=remote_id,
                content_hash="b" * 64,
                target_device_id=None,
                available_at=NOW,
                payload={},
                now=NOW,
            )


def test_work_item_transition_is_append_only(local_engine: Engine) -> None:
    with _uow(local_engine) as uow:
        item = uow.work_items.add(
            kind=WorkItemKind.QUICK_TEST,
            remote_id=uuid4(),
            content_hash="c" * 64,
            target_device_id=uuid4(),
            available_at=NOW,
            payload={},
            now=NOW,
        )
        queued = uow.work_items.transition(
            item.work_item_id,
            expected=WorkItemStatus.RECEIVING,
            target=WorkItemStatus.QUEUED,
            now=NOW + timedelta(seconds=1),
            reason_code="persisted",
        )
        assert queued.status is WorkItemStatus.QUEUED

    with local_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(LocalTransitionRow)) == 2


def test_outbox_claim_confirm_and_expired_claim_recovery(local_engine: Engine) -> None:
    first_id = uuid4()
    second_id = uuid4()
    with _uow(local_engine) as uow:
        uow.outbox.enqueue(
            report_id=first_id,
            kind=ReportKind.PACKAGE_RECEIPT,
            payload={"value": 1},
            now=NOW,
        )
        uow.outbox.enqueue(
            report_id=second_id,
            kind=ReportKind.ATTEMPT_RESULT,
            payload={"value": 2},
            now=NOW,
        )
    with _uow(local_engine) as uow:
        claimed = uow.outbox.claim_batch(
            worker_id="worker-a",
            now=NOW,
            lease_duration=timedelta(seconds=30),
            limit=1,
        )
        assert len(claimed) == 1
        assert claimed[0].status is OutboxStatus.CLAIMED
        assert claimed[0].claimed_until is not None
        assert claimed[0].claimed_until.tzinfo is UTC
        assert uow.outbox.confirm(claimed[0].report_id, worker_id="worker-a", now=NOW)
        remaining_id = ({first_id, second_id} - {claimed[0].report_id}).pop()
    with _uow(local_engine) as uow:
        second = uow.outbox.claim_batch(
            worker_id="worker-b",
            now=NOW,
            lease_duration=timedelta(seconds=1),
            limit=50,
        )
        assert [item.report_id for item in second] == [remaining_id]
    with _uow(local_engine) as uow:
        recovered = uow.outbox.claim_batch(
            worker_id="worker-c",
            now=NOW + timedelta(seconds=2),
            lease_duration=timedelta(seconds=10),
            limit=50,
        )
        assert [item.report_id for item in recovered] == [remaining_id]
        assert recovered[0].attempt_count == 2


def test_outbox_report_id_cannot_change_meaning(local_engine: Engine) -> None:
    report_id = uuid4()
    with _uow(local_engine) as uow:
        uow.outbox.enqueue(
            report_id=report_id,
            kind=ReportKind.ATTEMPT_START,
            payload={"attempt": "one"},
            now=NOW,
        )
    with _uow(local_engine) as uow, pytest.raises(ValueError, match="different content"):
        uow.outbox.enqueue(
            report_id=report_id,
            kind=ReportKind.ATTEMPT_START,
            payload={"attempt": "two"},
            now=NOW,
        )


def test_local_artifact_claim_recovery_and_permanent_failure(local_engine: Engine) -> None:
    with _uow(local_engine) as uow:
        work = uow.work_items.add(
            kind=WorkItemKind.FORMAL_TASK,
            remote_id=uuid4(),
            content_hash="d" * 64,
            target_device_id=uuid4(),
            available_at=NOW,
            payload={},
            now=NOW,
        )
        artifact_id = uuid4()
        uow.artifacts.add_many(
            (
                NewLocalArtifact(
                    artifact_id=artifact_id,
                    work_item_id=work.work_item_id,
                    owner_kind="formal_attempt",
                    owner_id=uuid4(),
                    artifact_kind="screenshot",
                    file_name="script-step-01.png",
                    relative_path=f"artifacts/{work.work_item_id}/{artifact_id}.png",
                    sha256="e" * 64,
                    size_bytes=10,
                    media_type="image/png",
                ),
            ),
            now=NOW,
        )
    with _uow(local_engine) as uow:
        claimed = uow.artifacts.claim_batch(
            worker_id="worker-a",
            now=NOW,
            lease_duration=timedelta(seconds=1),
            limit=20,
        )
        assert claimed[0].status is LocalArtifactStatus.CLAIMED
    with _uow(local_engine) as uow:
        recovered = uow.artifacts.claim_batch(
            worker_id="worker-b",
            now=NOW + timedelta(seconds=2),
            lease_duration=timedelta(seconds=30),
            limit=20,
        )
        assert recovered[0].attempt_count == 2
        assert uow.artifacts.release_failed(
            artifact_id,
            worker_id="worker-b",
            available_at=NOW + timedelta(seconds=30),
            error_code="artifact_changed",
            permanent=True,
        )
    with _uow(local_engine) as uow:
        failed = uow.artifacts.get(artifact_id)
    assert failed is not None
    assert failed.status is LocalArtifactStatus.DEAD_LETTER
    assert failed.last_error_code == "artifact_changed"
