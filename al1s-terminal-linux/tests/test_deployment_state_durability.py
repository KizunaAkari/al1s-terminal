import os
from pathlib import Path

import pytest

from terminal_deployer.state import DeploymentStateStore


def test_state_is_synced_before_atomic_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    replace = os.replace

    def tracked_replace(source: Path, target: Path) -> None:
        events.append("replace")
        replace(source, target)

    monkeypatch.setattr(os, "fsync", lambda _fd: events.append("sync"))
    monkeypatch.setattr(os, "replace", tracked_replace)
    store = DeploymentStateStore(tmp_path)
    store.write("release-1", status="candidate_starting")
    assert events[:2] == ["sync", "replace"]
    if os.name == "posix":
        assert events == ["sync", "replace", "sync"]
    assert store.read("release-1")["status"] == "candidate_starting"


def test_failed_sync_does_not_replace_previous_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = DeploymentStateStore(tmp_path)
    store.write("release-1", status="preflight")

    def failed_sync(_fd: int) -> None:
        raise OSError("disk error")

    monkeypatch.setattr(os, "fsync", failed_sync)
    with pytest.raises(OSError, match="disk error"):
        store.write("release-1", status="candidate_starting")
    assert store.read("release-1")["status"] == "preflight"
