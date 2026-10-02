"""Read-only upgrade status shared by maintenance health and risk checks."""

from __future__ import annotations

import importlib
import math
import time
from pathlib import Path

from terminal_deployer.state import DeploymentStateStore


def upgrade_status(state_directory: Path) -> tuple[str | None, bool]:
    store = DeploymentStateStore(state_directory)
    identity = store.unresolved_upgrade()
    if identity is None:
        return None, False
    lock_path = state_directory.parent / "deployment.lock"
    if not lock_path.is_file():
        return identity, False
    fcntl = importlib.import_module("fcntl")
    with lock_path.open("rb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            state = store.read(identity) or {}
            started = state.get("execution_started_at")
            within_deadline = (
                not isinstance(started, (int, float))
                or not math.isfinite(started)
                or time.time() < started + 1800
            )
            return identity, within_deadline
        else:
            fcntl.flock(lock, fcntl.LOCK_UN)
            return identity, False
