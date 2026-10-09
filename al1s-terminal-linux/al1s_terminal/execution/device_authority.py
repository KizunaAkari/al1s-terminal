"""One runtime owner and short manual-input grants; offline task permits stay separate."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from threading import RLock
from typing import IO
from uuid import UUID, uuid4


class PathRuntimeOwner:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._path = directory / "provider-confirmed.epoch"
        self._lock: IO[bytes] = (directory / "provider-owner.lock").open("a+b")
        if sys.platform == "win32":
            import msvcrt

            self._lock.seek(0)
            self._lock.write(b"0")
            self._lock.flush()
            self._lock.seek(0)
            msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.instance = uuid4()
        self.previous = None
        if self._path.is_file() and self._path.stat().st_size == 36:
            self.previous = UUID(self._path.read_text(encoding="ascii"))

    def confirm(self) -> None:
        temporary = self._path.with_suffix(".tmp")
        with temporary.open("wb") as output:
            output.write(str(self.instance).encode("ascii"))
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self._path)

    def close(self) -> None:
        self._lock.close()


class DeviceInputAuthority:
    def __init__(self) -> None:
        self._lock = RLock()
        self._grants: dict[str, tuple[bool, float]] = {}

    def update(self, serial: str, allowed: bool) -> None:
        with self._lock:
            self._grants[serial] = allowed, time.monotonic() + 25

    def allowed(self, serial: str) -> bool:
        with self._lock:
            value = self._grants.get(serial)
            # Legacy single-path operation remains unchanged until the new API confirms a path.
            return value is None or (value[0] and time.monotonic() < value[1])
