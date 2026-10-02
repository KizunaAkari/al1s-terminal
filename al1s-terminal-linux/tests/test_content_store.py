from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from al1s_terminal.execution.content_store import (
    ContentAddressedStore,
    ContentStoreError,
)


def test_blob_download_resumes_partial_and_commits_verified_content(tmp_path: Path) -> None:
    body = b"resume-this-resource"
    sha256 = hashlib.sha256(body).hexdigest()
    store = ContentAddressedStore(tmp_path)
    relative_path = store.blob_relative_path(sha256)
    final_path = tmp_path / relative_path
    final_path.parent.mkdir(parents=True)
    partial = final_path.with_name(f".{sha256}.part")
    partial.write_bytes(body[:7])
    ranges: list[tuple[int, int]] = []

    def download(start: int, end: int) -> bytes:
        ranges.append((start, end))
        return body[start : end + 1]

    result = store.ensure_blob(
        sha256=sha256,
        size_bytes=len(body),
        download_range=download,
    )

    assert result == relative_path
    assert ranges == [(7, len(body) - 1)]
    assert final_path.read_bytes() == body
    assert not partial.exists()


def test_hash_failure_never_creates_ready_blob(tmp_path: Path) -> None:
    body = b"actual"
    expected_sha = hashlib.sha256(b"expected").hexdigest()
    store = ContentAddressedStore(tmp_path)

    with pytest.raises(ContentStoreError, match="hash"):
        store.ensure_blob(
            sha256=expected_sha,
            size_bytes=len(body),
            download_range=lambda start, end: body[start : end + 1],
        )

    assert not (tmp_path / store.blob_relative_path(expected_sha)).exists()
    assert not list((tmp_path / "blobs").rglob("*.part"))
