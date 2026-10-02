import subprocess
from pathlib import Path

import pytest

from terminal_deployer.runner import (
    CommandResult,
    DeadlineCommandRunner,
    DeploymentDeadlineExceeded,
)


class Runner:
    def __init__(self, clock: list[float], elapsed: float = 0) -> None:
        self.clock, self.elapsed = clock, elapsed
        self.timeouts: list[float] = []

    def run(self, command: list[str], *, cwd: Path | None = None,
            env: dict[str, str] | None = None, timeout: float = 120,
            check: bool = True) -> CommandResult:
        self.timeouts.append(timeout)
        self.clock[0] += self.elapsed
        return CommandResult(0)


def test_total_budget_caps_each_command_without_reset() -> None:
    clock = [0.0]
    underlying = Runner(clock, elapsed=100)
    runner = DeadlineCommandRunner(underlying, 1800, lambda: clock[0])
    runner.run(["docker", "load"], timeout=900)
    clock[0] = 1790
    underlying.elapsed = 0
    runner.run(["docker", "inspect"], timeout=30)
    assert underlying.timeouts == [900, 10]
    clock[0] = 1800
    with pytest.raises(DeploymentDeadlineExceeded):
        runner.run(["docker", "restart"], check=False)
    assert len(underlying.timeouts) == 2


def test_late_success_does_not_hide_expired_budget() -> None:
    clock = [1799.0]
    underlying = Runner(clock, elapsed=2)
    runner = DeadlineCommandRunner(underlying, 1800, lambda: clock[0])
    with pytest.raises(DeploymentDeadlineExceeded):
        runner.run(["docker", "inspect"])


def test_subcommand_timeout_before_overall_deadline_is_preserved() -> None:
    class TimeoutRunner(Runner):
        def run(self, command: list[str], **kwargs: object) -> CommandResult:
            raise subprocess.TimeoutExpired(command, 10)

    runner = DeadlineCommandRunner(TimeoutRunner([0.0]), 1800, lambda: 20)
    with pytest.raises(subprocess.TimeoutExpired):
        runner.run(["docker", "inspect"])
