from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import Engine, event, insert
from sqlalchemy.orm import Session

from al1s_terminal.persistence.models import OutboxReportRow
from al1s_terminal.persistence.report_ordering import execution_report_head
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.types import ReportKind


def test_execution_head_backoff_and_claim_do_not_block_receipts(local_engine: Engine) -> None:
    now = datetime(2026, 9, 7, tzinfo=UTC)
    first, second, receipt = uuid4(), uuid4(), uuid4()
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        for report_id, kind, at in (
            (first, ReportKind.ATTEMPT_START, now),
            (second, ReportKind.ATTEMPT_RESULT, now + timedelta(seconds=1)),
            (receipt, ReportKind.PACKAGE_RECEIPT, now),
        ):
            uow.outbox.enqueue(report_id=report_id, kind=kind, payload={}, now=at)
    claim_args = dict(
        now=now + timedelta(seconds=2), lease_duration=timedelta(seconds=30), limit=50
    )
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        claimed = uow.outbox.claim_batch(worker_id="a", **claim_args)
        assert {report.report_id for report in claimed} == {first, receipt}
        assert uow.outbox.confirm(receipt, worker_id="a", now=now)
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        assert uow.outbox.claim_batch(worker_id="b", **claim_args) == ()
        uow.outbox.release_failed(
            first, worker_id="a", available_at=now + timedelta(minutes=1), error_code="offline"
        )
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        assert uow.outbox.claim_batch(worker_id="b", **claim_args) == ()
        resumed = uow.outbox.claim_batch(
            worker_id="b", **{**claim_args, "now": now + timedelta(minutes=2)}
        )
        assert [report.report_id for report in resumed] == [first]
        assert uow.outbox.confirm(first, worker_id="b", now=now + timedelta(minutes=2))
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        assert [
            report.report_id for report in uow.outbox.claim_batch(worker_id="b", **claim_args)
        ] == [second]


def test_report_head_is_bounded_and_does_not_skip_dead_letter(local_engine: Engine) -> None:
    now = datetime(2026, 9, 7, tzinfo=UTC)
    blocked_id = str(uuid4())
    rows = [
        dict(
            report_id=str(uuid4()),
            kind="attempt_result",
            payload={},
            status="confirmed",
            available_at=now,
            created_at=now,
            attempt_count=1,
            row_version=1,
        )
        for _ in range(5000)
    ]
    rows.extend(
        [
            dict(
                report_id=blocked_id,
                kind="attempt_start",
                payload={},
                status="dead_letter",
                available_at=now,
                created_at=now,
                attempt_count=1,
                row_version=1,
            ),
            dict(
                report_id=str(uuid4()),
                kind="attempt_result",
                payload={},
                status="pending",
                available_at=now,
                created_at=now,
                attempt_count=0,
                row_version=1,
            ),
        ]
    )
    with Session(local_engine) as session, session.begin():
        session.execute(insert(OutboxReportRow), rows)
    selects = []

    def capture(_conn, _cursor, statement, parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append((statement, parameters))

    event.listen(local_engine, "before_cursor_execute", capture)
    try:
        with Session(local_engine) as session:
            assert execution_report_head(session) == blocked_id
    finally:
        event.remove(local_engine, "before_cursor_execute", capture)
    assert len(selects) == 1
    statement, parameters = selects[0]
    assert "LIMIT" in statement
    with local_engine.connect() as connection:
        plan = connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).all()
    assert any("ix_outbox_" in str(row) for row in plan)
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        assert (
            uow.outbox.claim_batch(
                worker_id="test", now=now, lease_duration=timedelta(seconds=30), limit=50
            )
            == ()
        )
