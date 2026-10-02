"""Explicit post-acceptance cleanup, never called by the health check."""

import shutil

from terminal_deployer.models import DeploymentError, HostDeploymentConfig
from terminal_deployer.state import DeploymentStateStore


def cleanup_accepted_preflight(config: HostDeploymentConfig, deployment_id: str) -> None:
    states = DeploymentStateStore(config.state_dir)
    state = states.read(deployment_id)
    if state is None or state.get("status") != "succeeded":
        raise DeploymentError("only a successful, explicitly accepted deployment can be cleaned")
    root = config.deployment_dir.resolve()
    directory = config.deployment_dir / deployment_id
    target = directory / "database-preflight"
    if directory.is_symlink() or target.is_symlink() or not target.resolve().is_relative_to(root):
        raise DeploymentError("preflight cleanup path is outside the deployment directory")
    # Preflight contains DB copies only. Reject links rather than following them.
    if target.exists() and any(item.is_symlink() for item in target.rglob("*")):
        raise DeploymentError("preflight cleanup cannot contain symlinks")
    states.write(deployment_id, acceptance_confirmed=True, preflight_cleanup="pending")
    if target.exists():
        shutil.rmtree(target)
    states.write(deployment_id, preflight_cleanup="completed", cleaned_path=str(target))
