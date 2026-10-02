from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.providers.adb import AdbProbeResult, AdbProvider
from al1s_terminal.providers.adb_recovery import AdbRecovery
from al1s_terminal.transport.models import (
    AdbObservationBatchPayload,
    AdbObservationPayload,
    DiscoverTargetIdentifierPayload,
)
from al1s_terminal.transport.platform import PlatformPort
from al1s_terminal.types import TargetDeviceObservationRecord


class TargetDeviceDiscoveryService:
    def __init__(
        self,
        *,
        adb: AdbProvider,
        platform: PlatformPort,
        secret_store: FileSecretStore,
        uow_factory: Callable[[], LocalUnitOfWork],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._adb = adb
        self._recovery = AdbRecovery(adb)
        self._platform = platform
        self._secret_store = secret_store
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def synchronize(self) -> tuple[AdbProbeResult, tuple[TargetDeviceObservationRecord, ...]]:
        probe = self._adb.probe()
        identity = self._secret_store.load()
        if identity is None or not probe.binary_available or probe.error_code is not None:
            return probe, ()
        observations: list[TargetDeviceObservationRecord] = []
        now = self._clock()
        for device in probe.devices:
            discovered = self._platform.discover_target_identifier(
                identity.terminal_id,
                identity.credential,
                DiscoverTargetIdentifierPayload(
                    identifier=device.serial, adb_state=device.state.value
                ),
            )
            with self._uow_factory() as uow:
                observations.append(
                    uow.target_devices.record(
                        adb_serial=device.serial,
                        identifier_id=discovered.identifier_id,
                        target_device_id=discovered.target_device_id,
                        identifier_row_version=discovered.row_version,
                        adb_state=device.state,
                        model=device.model,
                        now=now,
                    )
                )
        return probe, tuple(observations)

    def serial_for_target(self, target_device_id: UUID) -> str | None:
        with self._uow_factory() as uow:
            observation = uow.target_devices.get_by_target(target_device_id)
            return observation.adb_serial if observation is not None else None

    def report_presence(self) -> int:
        probe = self._adb.probe()
        identity = self._secret_store.load()
        if identity is None or not probe.binary_available or probe.error_code is not None:
            return 0
        with self._uow_factory() as uow:
            known = uow.target_devices.get_by_serials(
                tuple(device.serial for device in probe.devices)
            )
        # Newly attached phones have no platform identifier yet. Limit onboarding
        # work per cycle so a large ADB list cannot stall heartbeats or execution.
        self._recovery.observe(probe, known.keys())
        for new_count, device in enumerate(
            item for item in probe.devices if item.serial not in known
        ):
            if new_count >= 10:
                break
            discovered = self._platform.discover_target_identifier(
                identity.terminal_id,
                identity.credential,
                DiscoverTargetIdentifierPayload(
                    identifier=device.serial, adb_state=device.state.value
                ),
            )
            with self._uow_factory() as uow:
                known[device.serial] = uow.target_devices.record(
                    adb_serial=device.serial,
                    identifier_id=discovered.identifier_id,
                    target_device_id=discovered.target_device_id,
                    identifier_row_version=discovered.row_version,
                    adb_state=device.state,
                    model=device.model,
                    now=self._clock(),
                )
        items = [
            AdbObservationPayload(
                identifier_id=known[device.serial].identifier_id,
                adb_state=device.state.value,
            )
            for device in probe.devices
            if device.serial in known
        ]
        updated = 0
        for offset in range(0, len(items), 100):
            updated += self._platform.report_adb_observations(
                identity.terminal_id,
                identity.credential,
                AdbObservationBatchPayload(items=items[offset : offset + 100]),
            )
        return updated
