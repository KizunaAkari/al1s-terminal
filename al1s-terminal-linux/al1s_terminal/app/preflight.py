"""Local checks only: no registration, network calls, database writes or phone actions."""
from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from al1s_terminal.app.config import TerminalSettings


@dataclass(frozen=True)
class Check:
    name: str
    available: bool
    required: bool
    code: str


def inspect_environment(settings: TerminalSettings) -> dict[str, object]:
    data = settings.data_dir.resolve()
    parent = data
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    writable = parent.is_dir() and os.access(parent, os.W_OK | os.X_OK)
    try:
        free = shutil.disk_usage(parent).free
    except OSError:
        free = 0
    adb = settings.adb_path or (Path(found) if (found := shutil.which("adb")) else None)
    checks = [
        Check("data_directory", writable, True, "writable" if writable else "not_writable"),
        Check("storage", free >= settings.minimum_available_storage_bytes, True,
              "sufficient" if free >= settings.minimum_available_storage_bytes else "storage_low"),
        Check("adb", bool(adb and adb.is_file() and os.access(adb, os.X_OK)), False,
              "configured" if adb else "not_found"),
        Check("ocr_resources", bool(settings.maa_ocr_model_dir
              and settings.maa_ocr_model_dir.is_dir()), False, "directory_check_only"),
        Check("scrcpy_server", bool(settings.scrcpy_server_path
              and settings.scrcpy_server_path.is_file()), False, "file_check_only"),
        Check("identity", settings.secret_path.is_file()
              or bool(settings.registration_code and settings.registration_code.get_secret_value()),
              True, "credential_or_registration_required"),
    ]
    return {
        "ready": all(check.available for check in checks if check.required),
        "checks": [asdict(check) for check in checks],
        "available_storage_bytes": free,
        "note": "Local checks only; device and model readiness is reported at runtime.",
    }
