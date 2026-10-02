"""Read only the application icon resources from a connected Android APK."""

import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_STORED, ZipFile

from al1s_terminal.providers.adb import AdbProvider

PACKAGE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
APK_PATH = re.compile(r"/[A-Za-z0-9_./~+=-]+\.apk")
ICON_PATH = re.compile(r"res/[A-Za-z0-9_./-]+\.png")
ICON_LINE = re.compile(r"application-icon-(\d+):'([^']+)'", re.ASCII)
MAX_METADATA = 8 * 1024 * 1024
MAX_ICON = 2 * 1024 * 1024


def read_application_icon(adb: AdbProvider, serial: str, package_name: str) -> bytes:
    if not PACKAGE.fullmatch(package_name):
        raise ValueError("Invalid application package")
    adb.require_device(serial)
    result = subprocess.run(
        [adb.binary_path, "-s", serial, "shell", "pm", "path", package_name],
        capture_output=True, timeout=10, check=True,
    )
    paths = [line.removeprefix("package:").strip() for line in result.stdout.decode().splitlines()]
    apk = next(
        (path for path in paths if path.endswith("/base.apk")
         and APK_PATH.fullmatch(path) and ".." not in path),
        None,
    )
    if apk is None:
        raise ValueError("Base APK was not found")
    with TemporaryDirectory(prefix="al1s-icon-") as directory:
        metadata = Path(directory) / "metadata.apk"
        with ZipFile(metadata, "w", compression=ZIP_STORED) as archive:
            for name in ("AndroidManifest.xml", "resources.arsc"):
                archive.writestr(name, _apk_entry(adb, serial, apk, name, MAX_METADATA))
        listing = subprocess.run(
            ["aapt", "dump", "badging", str(metadata)],
            capture_output=True, timeout=10, check=True,
        ).stdout.decode("utf-8", "replace")
    candidates = sorted(
        ((int(density), name) for density, name in ICON_LINE.findall(listing)
         if ICON_PATH.fullmatch(name) and ".." not in name),
        reverse=True,
    )
    for _density, name in candidates:
        try:
            icon = _apk_entry(adb, serial, apk, name, MAX_ICON)
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if icon.startswith(b"\x89PNG\r\n\x1a\n"):
            return icon
    raise ValueError("No PNG application icon was available")


def _apk_entry(adb: AdbProvider, serial: str, apk: str, name: str, limit: int) -> bytes:
    result = subprocess.run(
        [adb.binary_path, "-s", serial, "exec-out", "unzip", "-p", apk, name],
        capture_output=True, timeout=15, check=True,
    )
    if not result.stdout or len(result.stdout) > limit:
        raise ValueError("APK resource exceeds the icon extraction limit")
    return result.stdout
