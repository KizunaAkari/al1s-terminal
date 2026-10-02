from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
DOWNLOAD_CHUNK_BYTES = 4 * 1024 * 1024


class ContentStoreError(RuntimeError):
    pass


class ContentAddressedStore:
    def __init__(self, data_dir: Path) -> None:
        self._root = data_dir.resolve()
        self._blob_root = self._root / "blobs" / "sha256"
        self._package_root = self._root / "packages"

    def blob_relative_path(self, sha256: str) -> str:
        self._validate_sha256(sha256)
        return (Path("blobs") / "sha256" / sha256[:2] / sha256).as_posix()

    def ensure_blob(
        self,
        *,
        sha256: str,
        size_bytes: int,
        download_range: Callable[[int, int], bytes],
    ) -> str:
        self._validate_sha256(sha256)
        if size_bytes < 0:
            raise ContentStoreError("resource size cannot be negative")
        relative_path = self.blob_relative_path(sha256)
        final_path = self._controlled_path(relative_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        self._require_controlled_parent(final_path)
        if final_path.exists():
            self._require_regular_file(final_path)
            self._verify_file(final_path, sha256, size_bytes)
            return relative_path

        temporary = final_path.with_name(f".{sha256}.part")
        if temporary.is_symlink():
            raise ContentStoreError("resource temporary path must not be a symlink")
        if temporary.exists() and not temporary.is_file():
            raise ContentStoreError("resource temporary path is not a regular file")
        current_size = temporary.stat().st_size if temporary.exists() else 0
        if current_size > size_bytes:
            temporary.unlink()
            current_size = 0
        mode = "ab" if current_size else "wb"
        with temporary.open(mode) as stream:
            while current_size < size_bytes:
                end_inclusive = min(
                    size_bytes - 1,
                    current_size + DOWNLOAD_CHUNK_BYTES - 1,
                )
                chunk = download_range(current_size, end_inclusive)
                expected = end_inclusive - current_size + 1
                if len(chunk) != expected:
                    raise ContentStoreError("resource range download was incomplete")
                stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
                current_size += len(chunk)
        try:
            self._verify_file(temporary, sha256, size_bytes)
        except ContentStoreError:
            temporary.unlink(missing_ok=True)
            raise
        os.replace(temporary, final_path)
        self._fsync_parent(final_path)
        return relative_path

    def write_package(self, package_id: UUID, body: dict[str, object]) -> str:
        return self._write_json("packages", package_id, body)

    def write_quick_test(self, session_id: UUID, body: dict[str, object]) -> str:
        return self._write_json("quick-tests", session_id, body)

    def path_for_relative(self, relative_path: str) -> Path:
        path = self._controlled_path(relative_path)
        self._require_regular_file(path)
        return path

    def read_json(self, relative_path: str) -> dict[str, Any]:
        path = self.path_for_relative(relative_path)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContentStoreError("stored JSON document is invalid") from exc
        if not isinstance(value, dict):
            raise ContentStoreError("stored JSON document must be an object")
        return value

    def delete_definition(self, relative_path: str) -> None:
        self._delete_controlled(relative_path, ("packages", "quick-tests"))

    def delete_blob(self, relative_path: str) -> None:
        self._delete_controlled(relative_path, ("blobs/sha256",))

    def _delete_controlled(self, relative_path: str, allowed_roots: tuple[str, ...]) -> None:
        normalized = Path(relative_path).as_posix()
        if not any(
            normalized == root or normalized.startswith(f"{root}/") for root in allowed_roots
        ):
            raise ContentStoreError("content path is outside the requested storage class")
        path = self._controlled_path(relative_path)
        if not path.exists():
            return
        self._require_regular_file(path)
        path.unlink()
        self._fsync_parent(path)

    def _write_json(self, directory: str, identity: UUID, body: dict[str, object]) -> str:
        relative_path = (Path(directory) / f"{identity}.json").as_posix()
        final_path = self._controlled_path(relative_path)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        self._require_controlled_parent(final_path)
        payload = json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if final_path.exists():
            self._require_regular_file(final_path)
            if final_path.read_bytes() != payload:
                raise ContentStoreError("package file already exists with different content")
            return relative_path
        temporary = final_path.with_name(f".{identity}.tmp")
        if temporary.exists() or temporary.is_symlink():
            raise ContentStoreError("package temporary path already exists")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, final_path)
            self._fsync_parent(final_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        return relative_path

    def _controlled_path(self, relative_path: str) -> Path:
        candidate = self._root.joinpath(*Path(relative_path).parts)
        if candidate.is_absolute() and not candidate.resolve(strict=False).is_relative_to(
            self._root
        ):
            raise ContentStoreError("content path escaped the controlled data root")
        return candidate

    def _require_controlled_parent(self, path: Path) -> None:
        if not path.parent.resolve().is_relative_to(self._root):
            raise ContentStoreError("content parent escaped the controlled data root")

    @staticmethod
    def _require_regular_file(path: Path) -> None:
        if path.is_symlink() or not path.is_file():
            raise ContentStoreError("content path is not a regular file")

    @staticmethod
    def _verify_file(path: Path, sha256: str, size_bytes: int) -> None:
        if path.stat().st_size != size_bytes:
            raise ContentStoreError("resource size does not match its manifest")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != sha256:
            raise ContentStoreError("resource hash does not match its manifest")

    @staticmethod
    def _validate_sha256(value: str) -> None:
        if SHA256_PATTERN.fullmatch(value) is None:
            raise ContentStoreError("resource SHA-256 is invalid")

    @staticmethod
    def _fsync_parent(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
