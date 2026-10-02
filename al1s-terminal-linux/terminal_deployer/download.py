"""Bounded downloads from a configured platform origin, never a command-supplied URL."""

from __future__ import annotations

import hashlib
import os
import shutil
import ssl
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, Field

from terminal_deployer.models import DeploymentError, DeploymentManifest
from terminal_deployer.paths import private_directory

CHUNK = 4 * 1024**2


class PublishedRelease(BaseModel):
    release_id: UUID
    state: str
    architecture: str
    size_bytes: int = Field(gt=0, le=5 * 1024**3)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_image: str
    expected_image_id: str

    def manifest(self, command_id: str) -> DeploymentManifest:
        if self.state != "published" or self.architecture != "arm64":
            raise DeploymentError("release_not_published_or_incompatible")
        manifest = DeploymentManifest(
            command_id,
            f"{self.release_id}.tar",
            self.sha256,
            self.candidate_image,
            self.expected_image_id,
        )
        manifest.validate()
        return manifest


class PlatformReleaseClient:
    def __init__(
        self,
        origin: str,
        terminal_id: UUID,
        token: str,
        *,
        ca_file: str | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        checkpoint: Callable[[], None] = lambda: None,
    ):
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in origin)
            or "\\" in origin
            or len(token) < 32
        ):
            raise DeploymentError("invalid_upgrade_platform_configuration")
        _ = parsed.port
        self.clock = clock
        self.checkpoint = checkpoint
        self.prefix = f"/api/v1/terminal/host-upgrades/{terminal_id}/releases/"
        self.client = httpx.Client(
            base_url=origin.rstrip("/"),
            trust_env=False,
            follow_redirects=False,
            verify=ssl.create_default_context(cafile=ca_file),
            headers={"Authorization": "Bearer " + token, "Accept-Encoding": "identity"},
            transport=transport,
            timeout=15,
        )

    def close(self) -> None:
        self.client.close()

    def metadata(self, release_id: UUID, deadline: float) -> PublishedRelease:
        with self.client.stream(
            "GET", self.prefix + str(release_id), timeout=self._remaining(deadline)
        ) as response:
            if response.status_code != 200:
                raise DeploymentError("release_metadata_unavailable")
            if response.headers.get("Content-Encoding", "identity") != "identity":
                raise DeploymentError("release_metadata_encoding_rejected")
            raw = bytearray()
            for chunk in response.iter_bytes(4096):
                self._remaining(deadline)
                raw.extend(chunk)
                if len(raw) > 16384:
                    raise DeploymentError("release_metadata_oversized")
        value = PublishedRelease.model_validate_json(raw)
        if value.release_id != release_id:
            raise DeploymentError("release_identity_mismatch")
        value.manifest("metadata-check")
        return value

    def download(self, release: PublishedRelease, directory: Path, deadline: float) -> Path:
        private_directory(directory)
        target = directory / f"{release.release_id}.tar"
        partial = directory / f".{release.release_id}.partial"
        for path in (target, partial):
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise DeploymentError("unsafe_upgrade_archive_path")
        if target.exists() and self._valid(target, release, deadline):
            return target
        # Never load partial files. Resume only the hash-bound immutable release.
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > release.size_bytes:
            raise DeploymentError("partial_archive_oversized")
        if shutil.disk_usage(directory).free < release.size_bytes - offset + 64 * 1024**2:
            raise DeploymentError("insufficient_upgrade_disk_space")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(partial, flags, 0o600), "ab") as stream:
            while offset < release.size_bytes:
                end = min(offset + CHUNK, release.size_bytes) - 1
                chunk = self._range(release, offset, end, deadline)
                stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
                offset = end + 1
        if not self._valid(partial, release, deadline):
            # Only this validated private staging file; cannot be installed.
            partial.unlink()
            raise DeploymentError("release_hash_mismatch")
        os.replace(partial, target)
        if os.name == "posix":
            descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return target

    def _range(self, release: PublishedRelease, start: int, end: int, deadline: float) -> bytes:
        with self.client.stream(
            "GET",
            self.prefix + str(release.release_id) + "/content",
            headers={"Range": f"bytes={start}-{end}"},
            timeout=self._remaining(deadline),
        ) as response:
            if (
                response.status_code != 206
                or response.headers.get("Content-Range")
                != f"bytes {start}-{end}/{release.size_bytes}"
                or response.headers.get("ETag") != f'"sha256:{release.sha256}"'
                or response.headers.get("Content-Encoding", "identity") != "identity"
            ):
                raise DeploymentError("release_range_response_mismatch")
            body = bytearray()
            for chunk in response.iter_raw(64 * 1024):
                self._remaining(deadline)
                body.extend(chunk)
                if len(body) > end - start + 1:
                    raise DeploymentError("release_range_oversized")
            if len(body) != end - start + 1:
                raise DeploymentError("release_range_incomplete")
            return bytes(body)

    def _remaining(self, deadline: float) -> float:
        self.checkpoint()
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise DeploymentError("upgrade_download_timeout")
        return min(15.0, remaining)

    def _valid(self, path: Path, release: PublishedRelease, deadline: float) -> bool:
        if path.stat().st_size != release.size_bytes:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024**2), b""):
                self._remaining(deadline)
                digest.update(chunk)
        return digest.hexdigest() == release.sha256
