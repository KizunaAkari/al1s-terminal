"""Recover only a repeatedly offline, known transport; never restart the shared server."""

from collections.abc import Callable, Collection
from dataclasses import dataclass
from time import monotonic

import structlog

from al1s_terminal.providers.adb import AdbProbeResult, AdbProvider
from al1s_terminal.types import AdbDeviceState


@dataclass
class _Offline:
    observations: int = 0
    attempts: int = 0
    retry_at: float = 0


class AdbRecovery:
    def __init__(self, adb: AdbProvider, clock: Callable[[], float] = monotonic) -> None:
        self._adb, self._clock = adb, clock
        self._offline: dict[str, _Offline] = {}

    def observe(self, probe: AdbProbeResult, known: Collection[str]) -> None:
        if probe.error_code or not probe.binary_available:
            return
        offline = {
            d.serial
            for d in probe.devices
            if d.serial in known and d.state is AdbDeviceState.OFFLINE
        }
        self._offline = {s: state for s, state in self._offline.items() if s in offline}
        # One recovery per cycle bounds load even with many attached transports.
        for serial in sorted(offline):
            state = self._offline.setdefault(serial, _Offline())
            state.observations += 1
            if state.observations < 2 or self._clock() < state.retry_at:
                continue
            state.attempts = min(state.attempts + 1, 3)
            state.retry_at = self._clock() + min(60, 15 * 2 ** (state.attempts - 1))
            recovered = self._adb.reconnect_offline(serial)
            structlog.get_logger().info(
                "adb_target_recovery",
                requested=recovered,
                retry_seconds=min(60, 15 * 2 ** (state.attempts - 1)),
            )
            break
