from __future__ import annotations

import json
import re
import subprocess
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from al1s_terminal.host_management.log_reader import recent_container_logs
from al1s_terminal.host_management.service import Observation
from al1s_terminal.host_management.upgrade_guard import upgrade_status


class LinuxHostDriver:
    def __init__(
        self, container: str, deployment_state: Path | None = None,
        log_directory: Path | None = None,
    ):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container):
            raise ValueError("invalid_container_name")
        self.container = container
        self.deployment_state = deployment_state
        self.log_directory = log_directory

    @staticmethod
    def _run(args: list[str], timeout: int = 10) -> str:
        result = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={"PATH": "/usr/bin:/bin"},
        )
        return result.stdout

    def observe(self) -> Observation:
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        unresolved, upgrading = (
            upgrade_status(self.deployment_state)
            if self.deployment_state is not None
            else (None, False)
        )
        try:
            payload = json.loads(
                self._run(
                    [
                        "/usr/bin/docker",
                        "inspect",
                        "--format",
                        '{"state":{{json .State}},"id":{{json .Id}}}',
                        self.container,
                    ]
                )
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            return Observation(boot, "", "", False, unresolved, upgrading)
        raw, identity = payload["state"], payload["id"]
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("invalid_container_identity")
        return Observation(
            boot_id=boot,
            container_id=identity,
            container_started_at=str(raw.get("StartedAt", "")),
            healthy=raw.get("Running") is True and raw.get("Health", {}).get("Status") == "healthy",
            unresolved_upgrade_id=unresolved,
            upgrade_in_progress=upgrading,
        )

    def logs(self) -> dict[str, object]:
        if self.log_directory is None:
            raise FileNotFoundError("container log store is not configured")
        now = datetime.now(UTC)
        return {
            "observed_at": now.isoformat(),
            "lines": _safe_log_lines(
                recent_container_logs(self.log_directory, self.container, now)
            ),
        }

    def execute(self, action: str, container_id: str) -> None:
        if container_id and not re.fullmatch(r"[0-9a-f]{64}", container_id):
            raise ValueError("invalid_container_identity")
        if action == "restart_container":
            if not container_id:
                raise ValueError("container_unavailable")
            self._run(["/usr/bin/docker", "restart", "--time", "90", container_id], 110)
        elif action == "restart_host":
            if container_id:
                with suppress(OSError, subprocess.SubprocessError):
                    self._run(["/usr/bin/docker", "stop", "--time", "90", container_id], 110)
            self._run(["/usr/bin/systemctl", "reboot"], 10)
        else:
            raise ValueError("unsupported_maintenance_action")

    def restore_container(self, container_id: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", container_id):
            raise ValueError("invalid_container_identity")
        self._run(["/usr/bin/docker", "start", container_id], 30)


_SENSITIVE_LOG_LINE = re.compile(
    rb"authorization|bearer|password|secret|token|credential|api[_-]?key|cookie",
    re.IGNORECASE,
)


def _safe_log_lines(raw: bytes) -> list[str]:
    bounded = raw[-12_000:]
    if len(raw) > len(bounded):
        newline = bounded.find(b"\n")
        bounded = bounded[newline + 1:] if newline >= 0 else b""
    lines = bounded.decode("utf-8", errors="replace").splitlines()[-100:]
    return [
        "[redacted]" if _SENSITIVE_LOG_LINE.search(line.encode("utf-8"))
        else line[:1000]
        for line in lines
    ]
