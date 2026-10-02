from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from terminal_deployer.models import DEPLOYMENT_ID, DeploymentError


class DeploymentStateStore:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def write(self, deployment_id: str, **values: Any) -> dict[str, Any]:
        target = self._path(deployment_id)
        current = self.read(deployment_id) or {"deployment_id": deployment_id}
        current.update(values)
        if values.get("status") in {"accepted", "verifying", "preflight", "cutover",
                                     "candidate_starting"}:
            current["last_phase"] = values["status"]
        # The host deployer intentionally supports Ubuntu 22.04's Python 3.10.
        current["updated_at"] = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        self._write_atomic(target, current)
        return current

    def _write_atomic(self, target: Path, current: dict[str, Any]) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(current, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        if os.name == "posix":
            directory_fd = os.open(self._directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

    def unresolved_upgrade(self) -> str | None:
        marker = self._directory / ".upgrade-owner.json"
        if not marker.exists():
            return self._legacy_unresolved()
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
            identity = value["deployment_id"]
            if not isinstance(identity, str) or not DEPLOYMENT_ID.fullmatch(identity):
                raise ValueError("invalid upgrade identity")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise DeploymentError("upgrade ownership is unreadable") from exc
        state = self.read(identity)
        if state and state.get("status") in {"succeeded", "rolled_back", "failed"}:
            return self._legacy_unresolved()
        return identity

    def _legacy_unresolved(self) -> str | None:
        if not self._directory.exists():
            return None
        # One-time compatibility check before the first indexed ownership record.
        # Bound work; an oversized old directory requires explicit operator review.
        with os.scandir(self._directory) as entries:
            for count, entry in enumerate(entries):
                if count >= 1000:
                    raise DeploymentError("deployment history requires ownership review")
                path = Path(entry.name)
                if path.suffix != ".json" or path.name.startswith("."):
                    continue
                state = self.read(path.stem)
                if state is None or state.get("status") not in {
                    "succeeded", "rolled_back", "failed",
                }:
                    return path.stem
        return None

    def claim_upgrade(self, deployment_id: str, *, boot_id: str | None = None) -> None:
        # Caller holds the host-wide deployment flock. Never clear this claim on reboot.
        self._path(deployment_id)
        if self.unresolved_upgrade() is not None:
            raise DeploymentError("previous_upgrade_unresolved")
        # Persist a recoverable claim before the pointer. A crash between these writes
        # is found by the bounded orphan-record scan; no live operation has started.
        self.write(deployment_id, status="accepted", host_boot_id=boot_id, recovery_protocol=1)
        self._write_atomic(
            self._directory / ".upgrade-owner.json", {"deployment_id": deployment_id},
        )

    def read(self, deployment_id: str) -> dict[str, Any] | None:
        target = self._path(deployment_id)
        if not target.is_file():
            return None
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeploymentError("deployment state is unreadable") from exc
        if not isinstance(payload, dict):
            raise DeploymentError("deployment state must be a JSON object")
        return payload

    def _path(self, deployment_id: str) -> Path:
        if DEPLOYMENT_ID.fullmatch(deployment_id) is None:
            raise DeploymentError("invalid deployment_id")
        return self._directory / f"{deployment_id}.json"
