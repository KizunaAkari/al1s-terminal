"""Host-owned download and deployment adapter; no arbitrary command fields."""

import time
from collections.abc import Callable
from uuid import UUID

from terminal_deployer.download import PlatformReleaseClient
from terminal_deployer.models import HostDeploymentConfig
from terminal_deployer.service import TerminalDeployer
from terminal_deployer.state import DeploymentStateStore


class UpgradeCancelled(RuntimeError):
    """Raised only at a cooperative pre-install checkpoint."""


class HostUpgradeExecutor:
    def __init__(
        self, config: HostDeploymentConfig, client_factory: Callable[[], PlatformReleaseClient]
    ):
        self.config, self.client_factory = config, client_factory
        self.states = DeploymentStateStore(config.state_dir)

    def run(self, identity: str, release_id: UUID, started_at: float) -> None:
        self.run_cancellable(identity, release_id, started_at, lambda: None, lambda: None)

    def run_cancellable(
        self,
        identity: str,
        release_id: UUID,
        started_at: float,
        checkpoint: Callable[[], None],
        seal: Callable[[], None],
    ) -> None:
        client = self.client_factory()
        client.checkpoint = checkpoint
        try:
            checkpoint()
            deadline = time.monotonic() + max(0, started_at + 1800 - time.time())
            release = client.metadata(release_id, deadline)
            deployer = TerminalDeployer(self.config)

            def prepare(budget: float) -> None:
                client.download(release, self.config.artifact_dir, budget)
                # Serialize cancellation with the transition to installation.
                seal()

            deployer.deploy(
                release.manifest(identity),
                prepare=prepare,
                timeout_seconds=deadline - time.monotonic(),
            )
        except UpgradeCancelled:
            self.states.write(identity, status="failed", error="upgrade_cancelled")
            raise
        finally:
            client.close()

    def result(self, identity: str) -> str | None:
        state = self.states.read(identity)
        if state is None:
            # No deployer claim means no Docker operation was allowed to start.
            # Called only after the worker exited or after daemon crash recovery.
            return "failed"
        status = state.get("status")
        if status == "failed" and state.get("error") == "upgrade_cancelled":
            return "cancelled"
        if status == "succeeded":
            return "succeeded"
        return "failed" if status in {"failed", "rolled_back"} else None
