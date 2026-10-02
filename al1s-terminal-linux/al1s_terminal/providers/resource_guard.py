from __future__ import annotations

import platform
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    available_memory_bytes: int
    available_storage_bytes: int
    architecture: str


@dataclass(frozen=True, slots=True)
class ResourceDecision:
    permitted: bool
    code: str | None
    diagnostic: dict[str, int | str]


class RuntimeResourceGuard:
    """Revalidate volatile memory and storage immediately before execution."""

    def __init__(
        self,
        *,
        data_dir: Path,
        minimum_available_memory_bytes: int,
        minimum_available_storage_bytes: int,
        recording_storage_reserve_bytes: int,
        probe: Callable[[], ResourceSnapshot] | None = None,
    ) -> None:
        self._data_dir = data_dir
        self._minimum_memory = minimum_available_memory_bytes
        self._minimum_storage = minimum_available_storage_bytes
        self._recording_storage = recording_storage_reserve_bytes
        self._probe = probe or self._probe_system

    def evaluate(
        self,
        requirements: Mapping[str, object],
        *,
        record_video: bool,
    ) -> ResourceDecision:
        snapshot = self._probe()
        required_memory = max(
            self._minimum_memory,
            _non_negative_integer(requirements.get("min_memory_bytes")),
        )
        required_storage = max(
            self._minimum_storage,
            _non_negative_integer(requirements.get("min_storage_bytes")),
        )
        if record_video:
            required_storage += self._recording_storage
        diagnostic: dict[str, int | str] = {
            "available_memory_bytes": snapshot.available_memory_bytes,
            "required_available_memory_bytes": required_memory,
            "available_storage_bytes": snapshot.available_storage_bytes,
            "required_available_storage_bytes": required_storage,
            "architecture": snapshot.architecture,
        }
        architectures = _strings(requirements.get("architectures"))
        if architectures and snapshot.architecture not in architectures:
            diagnostic["allowed_architectures"] = ",".join(architectures)
            return ResourceDecision(False, "terminal_architecture_changed", diagnostic)
        if snapshot.available_memory_bytes < required_memory:
            return ResourceDecision(False, "terminal_memory_pressure", diagnostic)
        if snapshot.available_storage_bytes < required_storage:
            return ResourceDecision(False, "terminal_storage_pressure", diagnostic)
        return ResourceDecision(True, None, diagnostic)

    def _probe_system(self) -> ResourceSnapshot:
        return ResourceSnapshot(
            available_memory_bytes=_available_memory_bytes(),
            available_storage_bytes=shutil.disk_usage(self._data_dir).free,
            architecture=platform.machine().lower() or "unknown",
        )


def _available_memory_bytes() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, IndexError, ValueError):
        pass
    return _windows_available_memory_bytes()


def _windows_available_memory_bytes() -> int:
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        windll = getattr(ctypes, "windll", None)
        if windll is not None and windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.available_physical)
    except (AttributeError, OSError):
        pass
    return 0


def _non_negative_integer(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("resource requirement must be a non-negative integer")
    return value


def _strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple) or any(not isinstance(item, str) for item in value):
        raise ValueError("architectures must be a string array")
    return tuple(str(item).lower() for item in value)
