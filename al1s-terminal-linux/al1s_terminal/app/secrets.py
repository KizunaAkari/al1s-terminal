from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from uuid import UUID

from al1s_terminal.types import SecureIdentity


class SecretStoreError(RuntimeError):
    pass


class FileSecretStore:
    """Atomic, non-logging storage for the once-returned terminal credential."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> SecureIdentity | None:
        if not self._path.exists():
            return None
        self._require_safe_file()
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
            return SecureIdentity(
                installation_id=UUID(str(value["installation_id"])),
                terminal_id=UUID(str(value["terminal_id"])),
                credential=str(value["credential"]),
                credential_epoch=int(value["credential_epoch"]),
                terminal_row_version=int(value["terminal_row_version"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SecretStoreError("terminal credential file is invalid") from exc

    def save(self, identity: SecureIdentity) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        body = json.dumps(
            {
                "installation_id": str(identity.installation_id),
                "terminal_id": str(identity.terminal_id),
                "credential": identity.credential,
                "credential_epoch": identity.credential_epoch,
                "terminal_row_version": identity.terminal_row_version,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
            os.chmod(self._path, 0o600)
            self._fsync_parent()
        finally:
            if temporary.exists():
                temporary.unlink()

    def delete(self) -> None:
        if self._path.exists():
            self._path.unlink()
            self._fsync_parent()

    def _require_safe_file(self) -> None:
        if self._path.is_symlink() or not self._path.is_file():
            raise SecretStoreError("terminal credential path is not a regular file")
        if os.name != "nt":
            mode = stat.S_IMODE(self._path.stat().st_mode)
            if mode & 0o077:
                raise SecretStoreError("terminal credential permissions must be 0600")

    def _fsync_parent(self) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(self._path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
