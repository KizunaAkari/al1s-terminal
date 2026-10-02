from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid5

from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.transport.delivery_models import (
    CommandAcknowledgementPayload,
    TerminalCommandPayload,
)
from al1s_terminal.types import CancellationOutcome

CANCELLATION_REPORT_NAMESPACE = UUID("c28dbb34-48d5-4280-adb8-e55b96577392")


class CancellationCoordinator:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], LocalUnitOfWork],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def acknowledge(self, command: TerminalCommandPayload) -> CommandAcknowledgementPayload:
        if command.kind != "cancel_requested" or command.package_id is None:
            raise ValueError("command is not a cancellation request")
        now = self._clock()
        reason = str(command.payload.get("reason") or "operator")
        with self._uow_factory() as uow:
            package = uow.packages.get(command.package_id)
            if package is None or package.attempt_id != command.attempt_id:
                raise ValueError("cancellation command has no matching local package")
            work = uow.work_items.record_cancellation(
                package.work_item_id,
                command_id=command.command_id,
                reason=reason,
                now=now,
            )
            if work.cancel_outcome is CancellationOutcome.CANCELLED_BEFORE_START:
                uow.offline_permits.revoke_unconsumed(work.work_item_id, now=now)
        assert work.cancel_outcome is not None
        return CommandAcknowledgementPayload(
            report_id=uuid5(CANCELLATION_REPORT_NAMESPACE, str(command.command_id)),
            outcome=work.cancel_outcome.value,
            occurred_at=work.cancel_requested_at or now,
        )
