from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Engine, text

from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.types import WorkItemKind, WorkItemStatus


def test_platform_fifo_ignores_reverse_local_arrival(local_engine: Engine) -> None:
    now = datetime(2026, 9, 7, tzinfo=UTC)
    received = []
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        for ordinal in (2, 1):
            work = uow.work_items.add(
                kind=WorkItemKind.FORMAL_TASK,
                remote_id=uuid4(),
                content_hash="a" * 64,
                target_device_id=None,
                available_at=now,
                queue_enqueued_at=now,
                queue_order_id=UUID(int=ordinal),
                payload={},
                now=now + timedelta(seconds=3 - ordinal),
            )
            uow.work_items.transition(
                work.work_item_id,
                expected=WorkItemStatus.RECEIVING,
                target=WorkItemStatus.QUEUED,
                now=now,
            )
            received.append(work.work_item_id)
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        first = uow.work_items.get_next_queued(now=now + timedelta(seconds=5))
        assert first is not None
        assert first.work_item_id == received[1]
    with local_engine.connect() as connection:
        plan = connection.execute(
            text(
                "EXPLAIN QUERY PLAN SELECT id FROM inbox_work_items WHERE status='queued' "
                "AND available_at <= :now "
                "ORDER BY available_at, queue_enqueued_at, queue_order_id LIMIT 1"
            ),
            {"now": now.isoformat()},
        ).all()
        assert any("ix_inbox_platform_fifo" in str(row) for row in plan)
