from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from al1s_terminal.persistence.models import QuickTestEventRow
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.transport.delivery_models import QuickTestEventPayload
from al1s_terminal.types import WorkItemKind

NOW = datetime(2026, 9, 30, 8, 0, 1, 123456, tzinfo=UTC)


def _append_session(engine: Engine, *, now: datetime, count: int = 1):
    session_id = uuid4()
    with LocalUnitOfWork.from_engine(engine) as uow:
        work = uow.work_items.add(
            kind=WorkItemKind.QUICK_TEST, remote_id=session_id,
            content_hash="1" * 64, target_device_id=None,
            available_at=now, payload={}, now=now,
        )
        for index in range(count):
            appended = uow.quick_test_events.append(
                work, kind="started" if index == 0 else "log", step_number=None,
                now=now + timedelta(seconds=index),
                code=None if index == 0 else "maa_step_execution_stalled",
            )
            assert appended is not None and appended.created_at.tzinfo is UTC
    return session_id


def test_committed_sqlite_event_restores_utc_on_wire(local_engine: Engine) -> None:
    session_id = _append_session(local_engine, now=NOW)
    with Session(local_engine) as session:
        stored = session.scalar(select(QuickTestEventRow))
        assert stored is not None and stored.created_at.tzinfo is None
        stored_time = stored.created_at

    with LocalUnitOfWork.from_engine(local_engine) as uow:
        (event,) = uow.quick_test_events.pending_batch()
    assert event.session_id == session_id and event.sequence == 1
    assert event.created_at == NOW and event.created_at.tzinfo is UTC
    payload = QuickTestEventPayload(
        sequence=event.sequence, kind=event.kind, created_at=event.created_at,
    )
    wire_time = payload.model_dump(mode="json")["created_at"]
    assert datetime.fromisoformat(wire_time) == NOW
    assert datetime.fromisoformat(wire_time).utcoffset() == timedelta(0)
    with Session(local_engine) as session:
        stored = session.scalar(select(QuickTestEventRow))
        assert stored is not None and stored.created_at == stored_time
        assert stored.confirmed_at is None


def test_retry_and_confirmation_preserve_events_and_advance_sessions(local_engine: Engine) -> None:
    first_id = _append_session(local_engine, now=NOW, count=3)
    second_id = _append_session(local_engine, now=NOW + timedelta(minutes=1))
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        first_batch = uow.quick_test_events.pending_batch(limit=2)
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        assert uow.quick_test_events.pending_batch(limit=2) == first_batch
        assert [(item.session_id, item.sequence) for item in first_batch] == [
            (first_id, 1), (first_id, 2),
        ]
        assert all(item.created_at.tzinfo is UTC for item in first_batch)
        assert uow.quick_test_events.confirm_batch(first_id, 2, NOW + timedelta(hours=1)) == 2
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        (remaining,) = uow.quick_test_events.pending_batch(limit=2)
        assert remaining.session_id == first_id and remaining.sequence == 3
        assert remaining.created_at == NOW + timedelta(seconds=2)
        assert remaining.code == "maa_step_execution_stalled"
        assert uow.quick_test_events.confirm_batch(first_id, 3, NOW + timedelta(hours=1)) == 1
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        (next_event,) = uow.quick_test_events.pending_batch()
        assert next_event.session_id == second_id and next_event.sequence == 1
        assert next_event.created_at == NOW + timedelta(minutes=1)
        assert uow.quick_test_events.confirm_batch(second_id, 1, NOW + timedelta(hours=1)) == 1
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        assert uow.quick_test_events.pending_batch() == ()
