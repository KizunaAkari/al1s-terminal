"""Formal execution reports preserve FIFO across retries and reconnects."""

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from al1s_terminal.persistence.models import InboxWorkItemRow, OutboxReportRow
from al1s_terminal.types import OutboxStatus, ReportKind


def execution_report_head(session: Session) -> str | None:
    """One bounded DB query; retry availability never changes causal order.

    Left join preserves malformed legacy reports for explicit diagnosis rather
    than silently dropping them. Normal formal reports always reference work.
    """
    return session.scalar(
        select(OutboxReportRow.report_id)
        .outerjoin(InboxWorkItemRow, InboxWorkItemRow.id == OutboxReportRow.work_item_id)
        .where(
            OutboxReportRow.status.in_(
                (
                    OutboxStatus.PENDING.value,
                    OutboxStatus.CLAIMED.value,
                    OutboxStatus.DEAD_LETTER.value,
                )
            ),
            OutboxReportRow.kind.in_(
                (ReportKind.ATTEMPT_START.value, ReportKind.ATTEMPT_RESULT.value)
            ),
        )
        .order_by(
            InboxWorkItemRow.available_at,
            InboxWorkItemRow.queue_enqueued_at,
            InboxWorkItemRow.queue_order_id,
            case((OutboxReportRow.kind == ReportKind.ATTEMPT_START.value, 0), else_=1),
            OutboxReportRow.created_at,
            OutboxReportRow.report_id,
        )
        .limit(1)
    )
