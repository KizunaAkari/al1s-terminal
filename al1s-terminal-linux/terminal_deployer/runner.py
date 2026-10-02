from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from terminal_deployer.models import DeploymentError


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    def run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 120.0,
        check: bool = True,
    ) -> CommandResult: ...


class SubprocessCommandRunner:
    def run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 120.0,
        check: bool = True,
    ) -> CommandResult:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=None if env is None else {**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        result = CommandResult(completed.returncode, completed.stdout, completed.stderr)
        if check and result.returncode:
            detail = (result.stderr or result.stdout or "command failed").strip()[-2_000:]
            raise DeploymentError(f"{command[0]} failed with {result.returncode}: {detail}")
        return result


class DeploymentDeadlineExceeded(DeploymentError):
    """The outcome of a timed-out Docker operation must be reconciled, not replayed."""


class LabelledDeploymentRunner:
    def __init__(self, runner: CommandRunner, identity: str):
        self.runner, self.identity = runner, identity

    def run(
        self, command: list[str], *, cwd: Path | None = None,
        env: dict[str, str] | None = None, timeout: float = 120.0,
        check: bool = True,
    ) -> CommandResult:
        if command[:2] == ["docker", "run"]:
            command = [*command[:2], "--label", f"io.al1s.deployment={self.identity}",
                       *command[2:]]
        return self.runner.run(command, cwd=cwd, env=env, timeout=timeout, check=check)


class DeadlineCommandRunner:
    def __init__(
        self, runner: CommandRunner, deadline: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runner, self._deadline, self._clock = runner, deadline, clock

    def run(
        self, command: list[str], *, cwd: Path | None = None,
        env: dict[str, str] | None = None, timeout: float = 120.0,
        check: bool = True,
    ) -> CommandResult:
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            raise DeploymentDeadlineExceeded("deployment_execution_timeout")
        try:
            result = self._runner.run(
                command, cwd=cwd, env=env, timeout=min(timeout, remaining), check=check,
            )
        except subprocess.TimeoutExpired as exc:
            if self._clock() >= self._deadline:
                raise DeploymentDeadlineExceeded("deployment_execution_timeout") from exc
            raise
        if self._clock() >= self._deadline:
            raise DeploymentDeadlineExceeded("deployment_execution_timeout")
        return result
