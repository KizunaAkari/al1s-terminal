from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from al1s_terminal.app.secrets import FileSecretStore, SecretStoreError
from al1s_terminal.types import SecureIdentity


def test_secret_store_round_trip_does_not_create_plaintext_side_files(tmp_path: Path) -> None:
    path = tmp_path / "secrets" / "terminal-credential"
    store = FileSecretStore(path)
    identity = SecureIdentity(uuid4(), uuid4(), "terminal-secret", 3, 8)

    store.save(identity)

    assert store.load() == identity
    assert list(path.parent.iterdir()) == [path]
    assert json.loads(path.read_text(encoding="utf-8"))["credential"] == "terminal-secret"
    if os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


def test_secret_store_rejects_symlink(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("Windows symlink creation requires optional privileges")
    target = tmp_path / "target"
    target.write_text("{}", encoding="utf-8")
    path = tmp_path / "terminal-credential"
    path.symlink_to(target)

    with pytest.raises(SecretStoreError, match="regular file"):
        FileSecretStore(path).load()
