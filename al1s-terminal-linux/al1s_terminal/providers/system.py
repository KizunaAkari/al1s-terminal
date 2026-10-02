from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from al1s_terminal.providers.adb import AdbProbeResult
from al1s_terminal.providers.maa import MaaProbeResult

if TYPE_CHECKING:
    from al1s_terminal.execution.maa_smoke import MaaReadinessResult
    from al1s_terminal.providers.rknn_smoke import RknnReadinessResult


@dataclass(frozen=True, slots=True)
class SystemCapability:
    os_name: str
    os_version: str
    architecture: str
    cpu_cores: int
    memory_bytes: int
    storage_available_bytes: int
    low_resource: bool
    provider_keys: tuple[str, ...]
    details: dict[str, Any]


def probe_system(data_dir: Path) -> SystemCapability:
    data_dir.mkdir(parents=True, exist_ok=True)
    memory_bytes = _memory_bytes()
    return SystemCapability(
        os_name=platform.system().lower() or "unknown",
        os_version=platform.release() or "unknown",
        architecture=platform.machine().lower() or "unknown",
        cpu_cores=max(1, os.cpu_count() or 1),
        memory_bytes=memory_bytes,
        storage_available_bytes=shutil.disk_usage(data_dir).free,
        low_resource=0 < memory_bytes <= 2 * 1024**3,
        provider_keys=(),
        details={"python": platform.python_version()},
    )


def include_adb_capability(
    system: SystemCapability,
    adb: AdbProbeResult,
) -> SystemCapability:
    online = adb.online_devices
    provider_keys = set(system.provider_keys)
    if adb.binary_available and adb.error_code is None and online:
        provider_keys.add("adb")
    details = dict(system.details)
    details["adb"] = {
        "binary_available": adb.binary_available,
        "binary_path": adb.binary_path,
        "online_devices": len(online),
        "offline_devices": sum(item.state.value == "offline" for item in adb.devices),
        "unauthorized_devices": sum(item.state.value == "unauthorized" for item in adb.devices),
        "models": sorted({item.model for item in online if item.model}),
        "error_code": adb.error_code,
        "diagnostic": adb.diagnostic,
    }
    return SystemCapability(
        os_name=system.os_name,
        os_version=system.os_version,
        architecture=system.architecture,
        cpu_cores=system.cpu_cores,
        memory_bytes=system.memory_bytes,
        storage_available_bytes=system.storage_available_bytes,
        low_resource=system.low_resource,
        provider_keys=tuple(sorted(provider_keys)),
        details=details,
    )


def include_maa_capability(
    system: SystemCapability,
    maa: MaaProbeResult,
    *,
    readiness: MaaReadinessResult | None = None,
    execution_ready: bool = False,
) -> SystemCapability:
    if readiness is not None:
        execution_ready = readiness.ready
    provider_keys = set(system.provider_keys)
    if maa.available and execution_ready:
        provider_keys.add("maa")
    if maa.available and maa.ocr_available and execution_ready:
        provider_keys.add("ocr")
    details = dict(system.details)
    details["maa"] = {
        "available": maa.available,
        "execution_ready": execution_ready,
        "version": maa.version,
        "ocr_available": maa.ocr_available,
        "ocr_model_dir": maa.ocr_model_dir,
        "error_code": maa.error_code,
        "diagnostic": maa.diagnostic,
        "smoke_device_serial": readiness.device_serial if readiness is not None else None,
        "smoke_cached": readiness.cached if readiness is not None else False,
        "smoke_error_code": readiness.error_code if readiness is not None else None,
        "smoke_diagnostic": readiness.diagnostic if readiness is not None else None,
    }
    return SystemCapability(
        os_name=system.os_name,
        os_version=system.os_version,
        architecture=system.architecture,
        cpu_cores=system.cpu_cores,
        memory_bytes=system.memory_bytes,
        storage_available_bytes=system.storage_available_bytes,
        low_resource=system.low_resource,
        provider_keys=tuple(sorted(provider_keys)),
        details=details,
    )


def include_rknn_capability(
    system: SystemCapability,
    readiness: RknnReadinessResult,
) -> SystemCapability:
    provider_keys = set(system.provider_keys)
    if readiness.ready:
        provider_keys.add("yolo")
    details = dict(system.details)
    details["rknn_yolo"] = {
        "available": readiness.ready,
        "model": readiness.model,
        "runtime_version": readiness.runtime_version,
        "inference_ms": readiness.inference_ms,
        "output_shapes": [list(shape) for shape in readiness.output_shapes],
        "smoke_cached": readiness.cached,
        "error_code": readiness.error_code,
        "diagnostic": readiness.diagnostic,
    }
    return SystemCapability(
        os_name=system.os_name,
        os_version=system.os_version,
        architecture=system.architecture,
        cpu_cores=system.cpu_cores,
        memory_bytes=system.memory_bytes,
        storage_available_bytes=system.storage_available_bytes,
        low_resource=system.low_resource,
        provider_keys=tuple(sorted(provider_keys)),
        details=details,
    )


def include_scrcpy_capability(
    system: SystemCapability,
    *,
    relay_available: bool,
    adb_available: bool,
) -> SystemCapability:
    available = relay_available and adb_available
    provider_keys = set(system.provider_keys)
    if available:
        provider_keys.add("scrcpy")
        provider_keys.add("editor-session-v1")
    details = dict(system.details)
    details["scrcpy"] = {
        "available": available,
        "relay_available": relay_available,
        "adb_available": adb_available,
        "transport": "scrcpy-managed-v1",
        "automation_mode": "view_only",
    }
    return SystemCapability(
        os_name=system.os_name,
        os_version=system.os_version,
        architecture=system.architecture,
        cpu_cores=system.cpu_cores,
        memory_bytes=system.memory_bytes,
        storage_available_bytes=system.storage_available_bytes,
        low_resource=system.low_resource,
        provider_keys=tuple(sorted(provider_keys)),
        details=details,
    )


def _memory_bytes() -> int:
    if not hasattr(os, "sysconf"):
        return 0
    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, TypeError, ValueError):
        return 0
    return max(0, pages * page_size)
