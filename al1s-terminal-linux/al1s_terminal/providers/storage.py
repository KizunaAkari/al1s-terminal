"""Cheap statvfs-style sampling of the actual runtime directory filesystem."""

import shutil
from pathlib import Path


def runtime_storage(data_dir: Path) -> dict[str, int | str] | None:
    try:
        directory = (data_dir / "runtime").resolve()
        probe = directory if directory.is_dir() else data_dir.resolve()
        usage = shutil.disk_usage(probe)
        return {"directory": str(directory), "total_bytes": usage.total,
                "used_bytes": usage.used, "available_bytes": usage.free}
    except OSError:
        return None
