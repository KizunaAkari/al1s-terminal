from __future__ import annotations

import io
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from al1s_terminal.transport.models import HeartbeatPayload, RegisterTerminalPayload
from al1s_terminal.transport.platform import (
    HttpPlatformClient,
    PlatformCredentialRejectedError,
    PlatformError,
    PlatformUnavailableError,
)


def test_artifact_network_failure_uses_recoverable_platform_error() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.WriteTimeout("upload timed out", request=request)

    with httpx.Client(transport=httpx.MockTransport(fail)) as http:
        client = HttpPlatformClient("http://platform.test", client=http)
        with pytest.raises(PlatformUnavailableError) as caught:
            client.upload_artifact("http://storage.test/upload", {}, io.BytesIO(b"file"))
        assert caught.value.code == "artifact_upload_unavailable"


def test_http_client_uses_platform_contract_and_bearer() -> None:
    terminal_id = uuid4()
    installation_id = uuid4()
    seen_authorization = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_authorization
        if request.url.path.endswith("/register"):
            return httpx.Response(
                201,
                json={
                    "terminal": {
                        "terminal_id": str(terminal_id),
                        "installation_id": str(installation_id),
                        "terminal_type": "linux",
                        "display_name": "test",
                        "service_status": "offline",
                        "acceptance_status": "accepting",
                        "agent_version": "0.1.0",
                        "current_capability_profile_id": None,
                        "row_version": 1,
                        "created_at": datetime.now(UTC).isoformat(),
                        "last_seen_at": None,
                    },
                    "credential": "secret",
                },
            )
        seen_authorization = request.headers["Authorization"]
        return httpx.Response(
            200,
            json={
                "terminal_id": str(terminal_id),
                "installation_id": str(installation_id),
                "terminal_type": "linux",
                "display_name": "test",
                "service_status": "online",
                "acceptance_status": "accepting",
                "agent_version": "0.1.0",
                "current_capability_profile_id": None,
                "row_version": 2,
                "created_at": datetime.now(UTC).isoformat(),
                "last_seen_at": datetime.now(UTC).isoformat(),
            },
        )

    http = httpx.Client(
        base_url="http://platform.test",
        transport=httpx.MockTransport(handler),
    )
    client = HttpPlatformClient("http://platform.test", client=http)
    registered = client.register(
        RegisterTerminalPayload(
            registration_code="r" * 20,
            installation_id=installation_id,
            display_name="test",
            agent_version="0.1.0",
        )
    )
    client.heartbeat(
        registered.terminal.terminal_id,
        registered.credential,
        HeartbeatPayload(expected_version=1, agent_version="0.1.0"),
    )
    assert seen_authorization == "Bearer secret"


def test_http_client_maps_structured_platform_error() -> None:
    http = httpx.Client(
        base_url="http://platform.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                401,
                json={"detail": {"code": "terminal_auth_failed", "message": "denied"}},
            )
        ),
    )
    client = HttpPlatformClient("http://platform.test", client=http)

    with pytest.raises(PlatformError) as captured:
        client.register(
            RegisterTerminalPayload(
                registration_code="r" * 20,
                installation_id=uuid4(),
                display_name="test",
                agent_version="0.1.0",
            )
        )
    assert captured.value.status_code == 401
    assert captured.value.code == "terminal_auth_failed"


def test_http_client_distinguishes_rejected_terminal_credential() -> None:
    http = httpx.Client(
        base_url="http://platform.test",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                401,
                json={
                    "code": "invalid_terminal_credential",
                    "message": "credential was revoked",
                },
            )
        ),
    )
    client = HttpPlatformClient("http://platform.test", client=http)

    with pytest.raises(PlatformCredentialRejectedError):
        client.heartbeat(
            uuid4(),
            "rejected",
            HeartbeatPayload(expected_version=1, agent_version="0.1.0"),
        )


def test_http_client_requests_terminal_scoped_mqtt_session() -> None:
    terminal_id = uuid4()
    issued_at = datetime(2026, 9, 1, 8, tzinfo=UTC)
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["authorization"] = request.headers["Authorization"]
        return httpx.Response(
            200,
            json={
                "session_id": str(uuid4()),
                "broker_host": "mqtt.example.test",
                "broker_port": 8883,
                "tls_enabled": True,
                "client_id": "terminal-client",
                "username": "terminal-user",
                "password": "ephemeral-secret",
                "topic": f"al1s/v1/terminals/{terminal_id}/hints",
                "qos": 1,
                "issued_at": issued_at.isoformat(),
                "expires_at": (issued_at.replace(hour=9)).isoformat(),
            },
        )

    http = httpx.Client(
        base_url="http://platform.test",
        transport=httpx.MockTransport(handler),
    )
    client = HttpPlatformClient("http://platform.test", client=http)

    session = client.issue_mqtt_session(terminal_id, "terminal-secret")

    assert seen == {
        "path": "/api/v1/terminal/mqtt-sessions",
        "authorization": "Bearer terminal-secret",
    }
    assert session.topic.endswith(f"/{terminal_id}/hints")
    assert session.password == "ephemeral-secret"


@pytest.mark.parametrize(
    "status,header,body",
    [(200, "", b"abc"), (206, "bytes 4-6/9", b"abc"), (206, "bytes 0-2/9", b"toolong")],
)
def test_blob_range_rejects_ignored_wrong_or_oversized_response(status, header, body):
    with httpx.Client(
        base_url="https://platform.test",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, headers={"Content-Range": header}, content=body)
        ),
    ) as http:
        client = HttpPlatformClient("https://platform.test", client=http)
        with pytest.raises(PlatformError) as caught:
            client.download_blob_range(uuid4(), "secret", uuid4(), start=0, end_inclusive=2)
        assert caught.value.code == "blob_range_invalid"


def test_blob_range_retains_typed_credential_failure():
    with (
        httpx.Client(
            base_url="https://platform.test",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(401, json={"code": "invalid_terminal_credential"})
            ),
        ) as http,
        pytest.raises(PlatformCredentialRejectedError),
    ):
        HttpPlatformClient("https://platform.test", client=http).download_blob_range(
            uuid4(), "secret", uuid4(), start=0, end_inclusive=2
        )
