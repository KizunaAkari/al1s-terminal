from __future__ import annotations

import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Engine

from al1s_terminal.app.device_discovery import TargetDeviceDiscoveryService
from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.providers.adb import AdbProvider
from al1s_terminal.transport.models import (
    AdbObservationBatchPayload,
    DiscoverTargetIdentifierPayload,
    TargetIdentifierPayload,
)
from al1s_terminal.types import AdbDeviceState, SecureIdentity

NOW = datetime(2026, 8, 31, 15, 0, tzinfo=UTC)


class FakeDiscoveryPlatform:
    def __init__(self, terminal_id: UUID, target_device_id: UUID) -> None:
        self.terminal_id = terminal_id
        self.target_device_id = target_device_id
        self.identifier_id = uuid4()
        self.identifiers: list[str] = []
        self.observation_batches: list[AdbObservationBatchPayload] = []

    def discover_target_identifier(
        self,
        terminal_id: UUID,
        credential: str,
        payload: DiscoverTargetIdentifierPayload,
    ) -> TargetIdentifierPayload:
        assert terminal_id == self.terminal_id
        assert credential == "credential"
        assert payload.adb_state == "device"
        self.identifiers.append(payload.identifier)
        return TargetIdentifierPayload(
            identifier_id=self.identifier_id,
            target_device_id=self.target_device_id,
            source_terminal_id=terminal_id,
            source_type="adb_serial",
            display_hint="pho…one",
            row_version=2,
            created_at=NOW,
            bound_at=NOW,
        )

    def report_adb_observations(
        self, terminal_id: UUID, credential: str, payload: AdbObservationBatchPayload
    ) -> int:
        assert terminal_id == self.terminal_id
        assert credential == "credential"
        self.observation_batches.append(payload)
        return len(payload.items)


def test_discovery_persists_explicit_platform_binding(local_engine: Engine, tmp_path: Path) -> None:
    terminal_id = uuid4()
    target_device_id = uuid4()
    secret_store = FileSecretStore(tmp_path / "secrets" / "terminal-credential")
    secret_store.save(
        SecureIdentity(
            installation_id=uuid4(),
            terminal_id=terminal_id,
            credential="credential",
            credential_epoch=1,
            terminal_row_version=1,
        )
    )

    def runner(command: Sequence[str], _timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            "phone-1 device model:Galaxy_S transport_id:1\n",
            "",
        )

    platform = FakeDiscoveryPlatform(terminal_id, target_device_id)
    service = TargetDeviceDiscoveryService(
        adb=AdbProvider(adb_path="adb", runner=runner),
        platform=platform,  # type: ignore[arg-type]
        secret_store=secret_store,
        uow_factory=lambda: LocalUnitOfWork.from_engine(local_engine),
        clock=lambda: NOW,
    )

    probe, observations = service.synchronize()

    assert len(probe.online_devices) == 1
    assert platform.identifiers == ["phone-1"]
    assert observations[0].target_device_id == target_device_id
    assert service.serial_for_target(target_device_id) == "phone-1"
    assert service.report_presence() == 1
    assert len(platform.observation_batches) == 1
    assert platform.observation_batches[0].items[0].identifier_id == platform.identifier_id
    assert platform.observation_batches[0].items[0].adb_state == "device"

    with LocalUnitOfWork.from_engine(local_engine) as uow:
        uow.target_devices.record(
            adb_serial="phone-1",
            identifier_id=platform.identifier_id,
            target_device_id=target_device_id,
            identifier_row_version=3,
            adb_state=AdbDeviceState.OFFLINE,
            model="Galaxy_S",
            now=NOW,
        )

    # The row is an identity binding, not proof that the device is currently online.
    # Every execution must validate the resolved serial against a fresh ADB probe.
    assert service.serial_for_target(target_device_id) == "phone-1"


def test_presence_onboards_newly_connected_phone(local_engine: Engine, tmp_path: Path) -> None:
    terminal_id = uuid4()
    target_device_id = uuid4()
    secret_store = FileSecretStore(tmp_path / "secrets" / "terminal-credential")
    secret_store.save(SecureIdentity(
        installation_id=uuid4(), terminal_id=terminal_id, credential="credential",
        credential_epoch=1, terminal_row_version=1,
    ))

    def runner(command: Sequence[str], _timeout: float) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "phone-new device\n", "")

    platform = FakeDiscoveryPlatform(terminal_id, target_device_id)
    service = TargetDeviceDiscoveryService(
        adb=AdbProvider(adb_path="adb", runner=runner),
        platform=platform,  # type: ignore[arg-type]
        secret_store=secret_store,
        uow_factory=lambda: LocalUnitOfWork.from_engine(local_engine),
        clock=lambda: NOW,
    )
    assert service.report_presence() == 1
    assert platform.identifiers == ["phone-new"]
    assert service.serial_for_target(target_device_id) == "phone-new"
