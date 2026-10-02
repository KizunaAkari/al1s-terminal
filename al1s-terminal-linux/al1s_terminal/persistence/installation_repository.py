from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from al1s_terminal.persistence.models import (
    TerminalInstallationRow,
)
from al1s_terminal.persistence.repository_records import (
    _installation,
)
from al1s_terminal.types import (
    InstallationRecord,
    RegistrationStatus,
)


class InstallationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self) -> InstallationRecord | None:
        row = self._session.get(TerminalInstallationRow, 1)
        return None if row is None else _installation(row)

    def add_unregistered(self, installation_id: UUID, agent_version: str, now: datetime) -> None:
        self._session.add(
            TerminalInstallationRow(
                singleton_id=1,
                installation_id=str(installation_id),
                terminal_id=None,
                registration_status=RegistrationStatus.UNREGISTERED.value,
                terminal_row_version=None,
                credential_epoch=0,
                agent_version=agent_version,
                capability_revision=0,
                last_capability_hash=None,
                row_version=1,
                created_at=now,
                updated_at=now,
            )
        )

    def mark_registered(
        self,
        *,
        terminal_id: UUID,
        terminal_row_version: int,
        credential_epoch: int,
        agent_version: str,
        now: datetime,
    ) -> InstallationRecord:
        row = self._session.get(TerminalInstallationRow, 1)
        if row is None:
            raise RuntimeError("terminal installation is missing")
        row.terminal_id = str(terminal_id)
        row.registration_status = RegistrationStatus.REGISTERED.value
        row.terminal_row_version = terminal_row_version
        row.credential_epoch = credential_epoch
        row.agent_version = agent_version
        row.updated_at = now
        row.row_version += 1
        self._session.flush()
        return _installation(row)

    def record_heartbeat(
        self, *, terminal_row_version: int, agent_version: str, now: datetime
    ) -> InstallationRecord:
        row = self._session.get(TerminalInstallationRow, 1)
        if row is None:
            raise RuntimeError("terminal installation is missing")
        row.terminal_row_version = terminal_row_version
        row.agent_version = agent_version
        row.updated_at = now
        row.row_version += 1
        self._session.flush()
        return _installation(row)

    def record_capability(
        self, *, revision: int, manifest_hash: str, now: datetime
    ) -> InstallationRecord:
        row = self._session.get(TerminalInstallationRow, 1)
        if row is None:
            raise RuntimeError("terminal installation is missing")
        row.capability_revision = revision
        row.last_capability_hash = manifest_hash
        row.updated_at = now
        row.row_version += 1
        self._session.flush()
        return _installation(row)

    def mark_credential_rejected(self, *, now: datetime) -> InstallationRecord:
        row = self._session.get(TerminalInstallationRow, 1)
        if row is None:
            raise RuntimeError("terminal installation is missing")
        row.registration_status = RegistrationStatus.CREDENTIAL_REJECTED.value
        row.updated_at = now
        row.row_version += 1
        self._session.flush()
        return _installation(row)
