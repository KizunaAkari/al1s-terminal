"""Explicit setup for systemd's restricted writable paths; never creates task data."""

import os
import stat

from terminal_deployer.models import DeploymentError, HostDeploymentConfig
from terminal_deployer.paths import private_directory


def prepare_host(config: HostDeploymentConfig) -> None:
    config.validate()
    if not config.root.is_absolute() or not config.root.is_dir():
        raise DeploymentError("existing_absolute_deployment_root_required")
    private_directory(config.root)
    # Missing identity/data mounts must not silently become an empty new terminal.
    for path in (config.data_dir, config.adb_home, config.cert_dir):
        if not path.is_dir() or path.is_symlink():
            raise DeploymentError("existing_terminal_directories_required")
    for path in (config.artifact_dir, config.deployment_dir, config.state_dir):
        private_directory(path)
    if config.lock_path.is_symlink() or (
        config.lock_path.exists() and not config.lock_path.is_file()
    ):
        raise DeploymentError("unsafe_upgrade_lock")
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(config.lock_path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o022:
            raise DeploymentError("unsafe_upgrade_lock")
        if os.name == "posix" and info.st_uid != getattr(os, "geteuid")():  # noqa: B009
            raise DeploymentError("unsafe_upgrade_lock")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
