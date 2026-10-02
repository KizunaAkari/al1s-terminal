from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

from al1s_terminal.app.config import TerminalSettings
from al1s_terminal.app.lifecycle import RegistrationRequiredError, TerminalLifecycleService
from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.persistence.models import TerminalInstallationRow
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.providers.system import SystemCapability, include_scrcpy_capability
from al1s_terminal.transport.models import (
    CapabilityProfilePayload,
    CapabilityProfileResult,
    HeartbeatPayload,
    RegisteredTerminalPayload,
    RegisterTerminalPayload,
    TerminalPayload,
)
from al1s_terminal.transport.platform import PlatformCredentialRejectedError
from al1s_terminal.types import RegistrationStatus

NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


class FakePlatform:
    def __init__(self) -> None:
        self.terminal_id = uuid4()
        self.installation_id: UUID | None = None
        self.row_version = 1
        self.register_calls = 0
        self.heartbeat_calls = 0
        self.capability_calls = 0
        self.reject_credentials = False

    def register(self, payload: RegisterTerminalPayload) -> RegisteredTerminalPayload:
        self.register_calls += 1
        self.installation_id = payload.installation_id
        return RegisteredTerminalPayload(
            terminal=self._terminal(),
            credential="terminal-credential",
        )

    def heartbeat(
        self, terminal_id: UUID, credential: str, payload: HeartbeatPayload
    ) -> TerminalPayload:
        if self.reject_credentials:
            raise PlatformCredentialRejectedError(
                401,
                "invalid_terminal_credential",
                "credential was revoked",
            )
        assert terminal_id == self.terminal_id
        assert credential == "terminal-credential"
        assert payload.expected_version == self.row_version
        self.heartbeat_calls += 1
        self.row_version += 1
        return self._terminal()

    def publish_capability(
        self,
        terminal_id: UUID,
        credential: str,
        payload: CapabilityProfilePayload,
    ) -> CapabilityProfileResult:
        assert terminal_id == self.terminal_id
        assert credential == "terminal-credential"
        self.capability_calls += 1
        return CapabilityProfileResult(
            profile_id=uuid4(),
            terminal_id=terminal_id,
            revision=payload.revision,
            manifest_hash="f" * 64,
            created_at=NOW,
        )

    def _terminal(self) -> TerminalPayload:
        assert self.installation_id is not None
        return TerminalPayload(
            terminal_id=self.terminal_id,
            installation_id=self.installation_id,
            terminal_type="linux",
            display_name="test terminal",
            service_status="online",
            acceptance_status="accepting",
            agent_version="0.1.0",
            current_capability_profile_id=None,
            row_version=self.row_version,
            created_at=NOW,
            last_seen_at=NOW,
        )


def _service(engine: Engine, tmp_path: Path, platform: FakePlatform) -> TerminalLifecycleService:
    settings = TerminalSettings(
        _env_file=None,
        data_dir=tmp_path,
        display_name="test terminal",
        agent_version="0.1.0",
        registration_code=SecretStr("r" * 20),
    )

    def uow_factory() -> LocalUnitOfWork:
        return LocalUnitOfWork.from_engine(engine)

    return TerminalLifecycleService(
        settings=settings,
        platform=platform,  # type: ignore[arg-type]
        secret_store=FileSecretStore(settings.secret_path),
        uow_factory=uow_factory,
        clock=lambda: NOW,
    )


def test_registration_persists_identity_and_restart_does_not_register_again(
    local_engine: Engine, tmp_path: Path
) -> None:
    platform = FakePlatform()
    service = _service(local_engine, tmp_path, platform)

    registered = service.register_if_needed()
    restarted = service.register_if_needed()

    assert registered.registration_status is RegistrationStatus.REGISTERED
    assert restarted.terminal_id == registered.terminal_id
    assert platform.register_calls == 1
    assert "terminal-credential" not in repr(restarted)


def test_heartbeat_and_capability_revision_are_persisted(
    local_engine: Engine, tmp_path: Path
) -> None:
    platform = FakePlatform()
    service = _service(local_engine, tmp_path, platform)
    service.register_if_needed()

    heartbeat = service.heartbeat()
    capability = SystemCapability(
        os_name="linux",
        os_version="test",
        architecture="aarch64",
        cpu_cores=8,
        memory_bytes=2 * 1024**3,
        storage_available_bytes=10 * 1024**3,
        low_resource=True,
        provider_keys=(),
        details={"python": "3.12"},
    )
    first = service.publish_capability(capability)
    second = service.publish_capability(capability)

    assert heartbeat.terminal_row_version == 2
    assert first.capability_revision == 1
    assert second.capability_revision == 1
    assert platform.heartbeat_calls == 1
    assert platform.capability_calls == 1


def test_secure_identity_repairs_database_after_post_registration_crash(
    local_engine: Engine, tmp_path: Path
) -> None:
    platform = FakePlatform()
    service = _service(local_engine, tmp_path, platform)
    registered = service.register_if_needed()

    with Session(local_engine) as session, session.begin():
        # Simulate an older local DB snapshot while the atomic secret file survived.
        row = session.get(TerminalInstallationRow, 1)
        assert row is not None
        row.terminal_id = None
        row.credential_epoch = 0
        row.registration_status = RegistrationStatus.UNREGISTERED.value

    repaired = service.ensure_installation()
    assert repaired.terminal_id == registered.terminal_id
    assert repaired.registration_status is RegistrationStatus.REGISTERED


def test_secure_identity_restores_installation_after_local_database_loss(
    local_engine: Engine, tmp_path: Path
) -> None:
    platform = FakePlatform()
    service = _service(local_engine, tmp_path, platform)
    registered = service.register_if_needed()

    with Session(local_engine) as session, session.begin():
        session.execute(delete(TerminalInstallationRow))

    repaired = service.ensure_installation()

    assert repaired.installation_id == registered.installation_id
    assert repaired.terminal_id == registered.terminal_id
    assert repaired.registration_status is RegistrationStatus.REGISTERED


def test_rejected_credential_stops_heartbeat_and_marks_local_identity(
    local_engine: Engine, tmp_path: Path
) -> None:
    platform = FakePlatform()
    service = _service(local_engine, tmp_path, platform)
    service.register_if_needed()
    platform.reject_credentials = True

    with pytest.raises(RegistrationRequiredError, match="new registration code"):
        service.heartbeat()

    with LocalUnitOfWork.from_engine(local_engine) as uow:
        installation = uow.installation.get()
        assert installation is not None
        assert installation.registration_status is RegistrationStatus.CREDENTIAL_REJECTED

    with pytest.raises(RegistrationRequiredError, match="not registered"):
        service.require_registered_identity()


def test_registered_identity_allows_offline_runtime_start(
    local_engine: Engine, tmp_path: Path
) -> None:
    platform = FakePlatform()
    service = _service(local_engine, tmp_path, platform)
    registered = service.register_if_needed()

    offline_identity = service.require_registered_identity()

    assert offline_identity.terminal_id == registered.terminal_id
    assert offline_identity.registration_status is RegistrationStatus.REGISTERED


def test_scrcpy_capability_requires_both_relay_and_adb() -> None:
    base = SystemCapability(
        os_name="linux",
        os_version="test",
        architecture="aarch64",
        cpu_cores=8,
        memory_bytes=2 * 1024**3,
        storage_available_bytes=10 * 1024**3,
        low_resource=True,
        provider_keys=("adb",),
        details={},
    )

    unavailable = include_scrcpy_capability(
        base,
        relay_available=True,
        adb_available=False,
    )
    available = include_scrcpy_capability(
        base,
        relay_available=True,
        adb_available=True,
    )

    assert "scrcpy" not in unavailable.provider_keys
    assert unavailable.details["scrcpy"]["available"] is False
    assert "scrcpy" in available.provider_keys
    assert available.details["scrcpy"]["automation_mode"] == "view_only"
