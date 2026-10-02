from __future__ import annotations

import subprocess
from collections.abc import Sequence

import pytest

from al1s_terminal.providers.adb import (
    AdbProvider,
    AdbProviderError,
    parse_adb_devices,
)
from al1s_terminal.types import AdbDeviceState


def _completed(
    command: Sequence[str],
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def test_parse_adb_devices_preserves_states_and_metadata() -> None:
    devices = parse_adb_devices(
        "List of devices attached\n"
        "phone-1 device product:test model:Galaxy_S transport_id:1\n"
        "phone-2 unauthorized usb:1-1 transport_id:2\n"
    )

    assert devices[0].serial == "phone-1"
    assert devices[0].state is AdbDeviceState.ONLINE
    assert devices[0].model == "Galaxy_S"
    assert devices[1].state is AdbDeviceState.UNAUTHORIZED


def test_provider_requires_binding_when_multiple_devices_are_online() -> None:
    def runner(command: Sequence[str], _timeout: float) -> subprocess.CompletedProcess[str]:
        return _completed(command, stdout="a device\nb device\n")

    provider = AdbProvider(adb_path="adb", runner=runner)

    with pytest.raises(AdbProviderError) as captured:
        provider.require_device()
    assert captured.value.code == "adb_device_ambiguous"
    assert provider.require_device("b").serial == "b"


def test_provider_reports_unauthorized_bound_device() -> None:
    def runner(command: Sequence[str], _timeout: float) -> subprocess.CompletedProcess[str]:
        return _completed(command, stdout="phone unauthorized\n")

    with pytest.raises(AdbProviderError) as captured:
        AdbProvider(adb_path="adb", runner=runner).require_device("phone")
    assert captured.value.code == "adb_device_unauthorized"
