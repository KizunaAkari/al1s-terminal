import hashlib
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from terminal_deployer.download import PlatformReleaseClient, PublishedRelease
from terminal_deployer.models import DeploymentError


def client_for(body, handler=None, **changes):
    identity = uuid4()
    payload = dict(
        release_id=str(identity),
        state="published",
        architecture="arm64",
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        candidate_image="al1s-terminal-next:v123",
        expected_image_id="sha256:" + "b" * 64,
    )
    payload.update(changes)
    calls = []

    def respond(request):
        assert request.headers["authorization"] == "Bearer " + "x" * 32
        calls.append(request)
        if request.url.path.endswith("/content"):
            start, end = map(int, request.headers["range"][6:].split("-"))
            if handler:
                return handler(request, start, end)
            return httpx.Response(
                206,
                stream=httpx.ByteStream(body[start : end + 1]),
                headers={
                    "Content-Range": f"bytes {start}-{end}/{len(body)}",
                    "ETag": f'"sha256:{payload["sha256"]}"',
                },
            )
        return httpx.Response(200, json=payload)

    return (
        PlatformReleaseClient(
            "https://platform.local",
            uuid4(),
            "x" * 32,
            transport=httpx.MockTransport(respond),
            clock=lambda: 100,
        ),
        identity,
        calls,
    )


def test_download_is_chunked_verified_and_cached(tmp_path):
    body = b"a" * (4 * 1024**2 + 23)
    client, identity, calls = client_for(body)
    try:
        release = client.metadata(identity, 200)
        directory = tmp_path / "artifacts"
        output = client.download(release, directory, 200)
        assert output.read_bytes() == body
        assert len(calls) == 3
        assert client.download(release, directory, 200) == output
        assert len(calls) == 3
    finally:
        client.close()


def test_partial_resume_still_checks_whole_hash(tmp_path):
    body = b"archive-bytes"
    client, identity, calls = client_for(body)
    directory = tmp_path / "artifacts"
    directory.mkdir(mode=0o700)
    partial = directory / f".{identity}.partial"
    partial.write_bytes(body[:4])
    try:
        release = client.metadata(identity, 200)
        assert client.download(release, directory, 200).read_bytes() == body
        assert calls[-1].headers["range"] == f"bytes=4-{len(body) - 1}"
    finally:
        client.close()


def test_hash_failure_never_creates_installable_archive(tmp_path):
    client, identity, _ = client_for(b"bad-content", sha256="a" * 64)
    try:
        with pytest.raises(DeploymentError, match="release_hash_mismatch"):
            client.download(client.metadata(identity, 200), tmp_path / "artifacts", 200)
        assert not (tmp_path / "artifacts" / f"{identity}.tar").exists()
    finally:
        client.close()


@pytest.mark.parametrize("status", [200, 302, 401, 500])
def test_wrong_range_or_redirect_is_not_followed(tmp_path, status):
    client, identity, calls = client_for(
        b"archive",
        lambda *_: httpx.Response(
            status, content=b"archive", headers={"Location": "https://other.local"}
        ),
    )
    try:
        with pytest.raises(DeploymentError, match="range_response"):
            client.download(client.metadata(identity, 200), tmp_path / "artifacts", 200)
        assert len(calls) == 2
    finally:
        client.close()


@pytest.mark.parametrize(
    "origin", ["http://host", "https://user:password@host", "https://host/api", "https://host?x=1"]
)
def test_only_configured_https_origin(origin):
    with pytest.raises(DeploymentError):
        PlatformReleaseClient(origin, uuid4(), "x" * 32)


def test_deadline_does_not_restart(tmp_path):
    client, identity, calls = client_for(b"archive")
    try:
        with pytest.raises(DeploymentError, match="download_timeout"):
            client.metadata(identity, 99)
        assert not calls
    finally:
        client.close()


def test_unpublished_and_other_architecture_rejected():
    data = dict(
        release_id=uuid4(),
        size_bytes=1,
        sha256="a" * 64,
        candidate_image="al1s-terminal-next:v1",
        expected_image_id="sha256:" + "b" * 64,
    )
    for state, arch in [("draft", "arm64"), ("published", "amd64")]:
        with pytest.raises(DeploymentError):
            PublishedRelease(**data, state=state, architecture=arch).manifest("upgrade1")


def test_low_disk_space_refuses_download_before_transfer(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "terminal_deployer.download.shutil.disk_usage", lambda _path: SimpleNamespace(free=0)
    )
    client, identity, calls = client_for(b"archive")
    try:
        with pytest.raises(DeploymentError, match="insufficient_upgrade_disk_space"):
            client.download(client.metadata(identity, 200), tmp_path / "artifacts", 200)
        assert len(calls) == 1
        assert not list((tmp_path / "artifacts").iterdir())
    finally:
        client.close()


def test_truncated_range_is_not_installable(tmp_path):
    body = b"archive"
    digest = hashlib.sha256(body).hexdigest()
    client, identity, _ = client_for(
        body,
        lambda _request, start, end: httpx.Response(
            206,
            stream=httpx.ByteStream(body[:-1]),
            headers={
                "Content-Range": f"bytes {start}-{end}/{len(body)}",
                "ETag": f'"sha256:{digest}"',
            },
        ),
    )
    try:
        with pytest.raises(DeploymentError):
            client.download(client.metadata(identity, 200), tmp_path / "artifacts", 200)
        assert not (tmp_path / "artifacts" / f"{identity}.tar").exists()
    finally:
        client.close()


def test_encoded_metadata_is_rejected_before_decompression():
    client = PlatformReleaseClient(
        "https://platform.local",
        uuid4(),
        "x" * 32,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, stream=httpx.ByteStream(b"not-gzip"), headers={"Content-Encoding": "gzip"}
            )
        ),
        clock=lambda: 100,
    )
    try:
        with pytest.raises(DeploymentError, match="metadata_encoding_rejected"):
            client.metadata(uuid4(), 200)
    finally:
        client.close()
