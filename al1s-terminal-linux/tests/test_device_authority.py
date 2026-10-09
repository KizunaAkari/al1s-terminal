from pathlib import Path

import pytest

from al1s_terminal.execution.device_authority import DeviceInputAuthority, PathRuntimeOwner


def test_manual_grants_expire_and_legacy_single_path_is_preserved(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr("al1s_terminal.execution.device_authority.time.monotonic", lambda: clock[0])
    grants = DeviceInputAuthority()
    assert grants.allowed("legacy")
    grants.update("phone", True)
    assert grants.allowed("phone")
    clock[0] = 35.0
    assert not grants.allowed("phone")
    grants.update("phone", False)
    assert not grants.allowed("phone")


def test_runtime_owner_excludes_second_process_and_retains_confirmed_epoch(tmp_path: Path):
    first = PathRuntimeOwner(tmp_path)
    try:
        with pytest.raises(OSError):
            PathRuntimeOwner(tmp_path)
        first.confirm()
        previous = first.instance
    finally:
        first.close()
    second = PathRuntimeOwner(tmp_path)
    try:
        assert second.previous == previous
        # A failed startup must not overwrite the last server-confirmed owner.
        assert (tmp_path / "provider-confirmed.epoch").read_text() == str(previous)
    finally:
        second.close()
