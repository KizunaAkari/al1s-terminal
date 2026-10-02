"""Shared standard-library-only path checks for the offline deployer and downloader."""

import os
import stat
from pathlib import Path

from terminal_deployer.models import DeploymentError


def private_directory(path: Path) -> None:
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise DeploymentError("unsafe_upgrade_directory")
        if parent.exists() and os.name == "posix":
            info = parent.stat()
            trusted_sticky_ancestor = (
                parent != path and info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            )
            effective_uid = getattr(os, "geteuid")()  # noqa: B009
            if not trusted_sticky_ancestor and (
                info.st_uid != effective_uid or stat.S_IMODE(info.st_mode) & 0o022
            ):
                raise DeploymentError("upgrade_directory_requires_private_ownership")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
