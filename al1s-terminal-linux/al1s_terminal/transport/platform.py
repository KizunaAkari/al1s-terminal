from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import UUID

import httpx

from al1s_terminal.transport.delivery_models import (
    ArtifactUploadPayload,
    AttemptResultPayload,
    AttemptResultReceiptPayload,
    AttemptStartPayload,
    AttemptStartResultPayload,
    CommandAcknowledgementPayload,
    CommandAcknowledgementResultPayload,
    CreateArtifactUploadPayload,
    PackageReceiptPayload,
    PackageReceiptResultPayload,
    QuickTestClaimPayload,
    QuickTestControlPayload,
    QuickTestEventBatchPayload,
    QuickTestEventReceiptPayload,
    QuickTestPagePayload,
    QuickTestResultPayload,
    QuickTestResultReceiptPayload,
    TaskPackagePayload,
    TerminalBlobMetadataPayload,
    TerminalCommandPagePayload,
    TerminalMqttSessionPayload,
)
from al1s_terminal.transport.models import (
    AdbObservationBatchPayload,
    CapabilityProfilePayload,
    CapabilityProfileResult,
    DiscoverTargetIdentifierPayload,
    HeartbeatPayload,
    RegisteredTerminalPayload,
    RegisterTerminalPayload,
    TargetIdentifierPayload,
    TerminalPayload,
)


class PlatformError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class PlatformCredentialRejectedError(PlatformError):
    pass


class PlatformUnavailableError(PlatformError):
    pass


class PlatformPort(Protocol):
    def register(self, payload: RegisterTerminalPayload) -> RegisteredTerminalPayload: ...

    def heartbeat(
        self, terminal_id: UUID, credential: str, payload: HeartbeatPayload
    ) -> TerminalPayload: ...

    def publish_capability(
        self,
        terminal_id: UUID,
        credential: str,
        payload: CapabilityProfilePayload,
    ) -> CapabilityProfileResult: ...

    def discover_target_identifier(
        self,
        terminal_id: UUID,
        credential: str,
        payload: DiscoverTargetIdentifierPayload,
    ) -> TargetIdentifierPayload: ...

    def report_adb_observations(
        self, terminal_id: UUID, credential: str, payload: AdbObservationBatchPayload
    ) -> int: ...


class HttpPlatformClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 15.0,
        ca_file: Path | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=timeout_seconds,
            trust_env=False,
            follow_redirects=False,
            verify=str(ca_file) if ca_file is not None else True,
        )

    def register(self, payload: RegisterTerminalPayload) -> RegisteredTerminalPayload:
        response = self._request(
            "POST", "/api/v1/terminals/register", json=payload.model_dump(mode="json")
        )
        return RegisteredTerminalPayload.model_validate(response.json())

    def heartbeat(
        self, terminal_id: UUID, credential: str, payload: HeartbeatPayload
    ) -> TerminalPayload:
        response = self._request(
            "POST",
            f"/api/v1/terminals/{terminal_id}/heartbeat",
            credential=credential,
            json=payload.model_dump(mode="json"),
        )
        return TerminalPayload.model_validate(response.json())

    def publish_capability(
        self,
        terminal_id: UUID,
        credential: str,
        payload: CapabilityProfilePayload,
    ) -> CapabilityProfileResult:
        response = self._request(
            "POST",
            f"/api/v1/terminals/{terminal_id}/capability-profiles",
            credential=credential,
            json=payload.model_dump(mode="json"),
        )
        return CapabilityProfileResult.model_validate(response.json())

    def discover_target_identifier(
        self,
        terminal_id: UUID,
        credential: str,
        payload: DiscoverTargetIdentifierPayload,
    ) -> TargetIdentifierPayload:
        response = self._request(
            "POST",
            "/api/v1/target-devices/discoveries",
            credential=credential,
            json=payload.model_dump(mode="json"),
        )
        return TargetIdentifierPayload.model_validate(response.json())

    def report_adb_observations(
        self, terminal_id: UUID, credential: str, payload: AdbObservationBatchPayload
    ) -> int:
        response = self._request(
            "POST",
            "/api/v1/target-devices/adb-observations",
            credential=credential,
            json=payload.model_dump(mode="json"),
        )
        return int(response.json()["updated"])

    def list_commands(
        self, terminal_id: UUID, credential: str, *, limit: int
    ) -> TerminalCommandPagePayload:
        response = self._request(
            "GET",
            "/api/v1/terminal/commands",
            credential=credential,
            params={"limit": str(limit)},
        )
        return TerminalCommandPagePayload.model_validate(response.json())

    def issue_mqtt_session(self, terminal_id: UUID, credential: str) -> TerminalMqttSessionPayload:
        response = self._request(
            "POST",
            "/api/v1/terminal/mqtt-sessions",
            credential=credential,
        )
        return TerminalMqttSessionPayload.model_validate(response.json())

    def acknowledge_command(
        self,
        terminal_id: UUID,
        credential: str,
        command_id: UUID,
        payload: CommandAcknowledgementPayload,
    ) -> CommandAcknowledgementResultPayload:
        response = self._request(
            "POST",
            f"/api/v1/terminal/commands/{command_id}/acknowledgement",
            credential=credential,
            json=payload.model_dump(mode="json"),
        )
        return CommandAcknowledgementResultPayload.model_validate(response.json())

    def get_task_package(
        self, terminal_id: UUID, credential: str, package_id: UUID
    ) -> TaskPackagePayload:
        response = self._request(
            "GET",
            f"/api/v1/terminal/task-packages/{package_id}",
            credential=credential,
        )
        return TaskPackagePayload.model_validate(response.json())

    def head_blob(
        self, terminal_id: UUID, credential: str, blob_id: UUID
    ) -> TerminalBlobMetadataPayload:
        response = self._request(
            "HEAD",
            f"/api/v1/terminal/blobs/{blob_id}",
            credential=credential,
        )
        return TerminalBlobMetadataPayload(
            blob_id=blob_id,
            sha256=response.headers["X-Content-SHA256"],
            size_bytes=int(response.headers["Content-Length"]),
            media_type=response.headers.get("Content-Type", "application/octet-stream"),
        )

    def download_blob_range(
        self,
        terminal_id: UUID,
        credential: str,
        blob_id: UUID,
        *,
        start: int,
        end_inclusive: int,
    ) -> bytes:
        response = self._request(
            "GET",
            f"/api/v1/terminal/blobs/{blob_id}",
            credential=credential,
            headers={"Range": f"bytes={start}-{end_inclusive}"},
            max_bytes=end_inclusive - start + 1,
        )
        expected = f"bytes {start}-{end_inclusive}/"
        if response.status_code != 206 or not response.headers.get("Content-Range", "").startswith(
            expected
        ):
            raise PlatformError(502, "blob_range_invalid", "resource range was not honored")
        return response.content

    def submit_package_receipt(
        self,
        terminal_id: UUID,
        credential: str,
        package_id: UUID,
        payload: PackageReceiptPayload,
    ) -> PackageReceiptResultPayload:
        response = self._request(
            "POST",
            f"/api/v1/terminal/task-packages/{package_id}/receipt",
            credential=credential,
            json=payload.model_dump(mode="json"),
        )
        return PackageReceiptResultPayload.model_validate(response.json())

    def start_attempt(
        self,
        terminal_id: UUID,
        credential: str,
        attempt_id: UUID,
        payload: AttemptStartPayload,
    ) -> AttemptStartResultPayload:
        response = self._request(
            "POST",
            f"/api/v1/terminal/attempts/{attempt_id}/start",
            credential=credential,
            json=payload.model_dump(mode="json"),
        )
        return AttemptStartResultPayload.model_validate(response.json())

    def submit_attempt_result(
        self,
        terminal_id: UUID,
        credential: str,
        attempt_id: UUID,
        payload: AttemptResultPayload,
    ) -> AttemptResultReceiptPayload:
        response = self._request(
            "POST",
            f"/api/v1/terminal/attempts/{attempt_id}/result",
            credential=credential,
            json=payload.model_dump(mode="json"),
        )
        return AttemptResultReceiptPayload.model_validate(response.json())

    def list_quick_tests(
        self, terminal_id: UUID, credential: str, *, limit: int
    ) -> QuickTestPagePayload:
        response = self._request(
            "GET",
            "/api/v1/terminal/quick-tests",
            credential=credential,
            params={"limit": str(limit)},
        )
        return QuickTestPagePayload.model_validate(response.json())

    def claim_quick_test(
        self, terminal_id: UUID, credential: str, session_id: UUID
    ) -> QuickTestClaimPayload:
        response = self._request(
            "POST",
            f"/api/v1/terminal/quick-tests/{session_id}/claim",
            credential=credential,
        )
        return QuickTestClaimPayload.model_validate(response.json())

    def get_quick_test_control(
        self, terminal_id: UUID, credential: str, session_id: UUID
    ) -> QuickTestControlPayload:
        response = self._request(
            "GET",
            f"/api/v1/terminal/quick-tests/{session_id}/control",
            credential=credential,
        )
        return QuickTestControlPayload.model_validate(response.json())

    def report_quick_test_events(
        self,
        terminal_id: UUID,
        credential: str,
        session_id: UUID,
        payload: QuickTestEventBatchPayload,
    ) -> QuickTestEventReceiptPayload:
        response = self._request(
            "POST",
            f"/api/v1/terminal/quick-tests/{session_id}/events",
            credential=credential,
            json=payload.model_dump(mode="json"),
        )
        return QuickTestEventReceiptPayload.model_validate(response.json())

    def submit_quick_test_result(
        self,
        terminal_id: UUID,
        credential: str,
        script_id: UUID,
        payload: QuickTestResultPayload,
        *,
        idempotency_key: str,
    ) -> QuickTestResultReceiptPayload:
        response = self._request(
            "POST",
            f"/api/v1/maa/scripts/{script_id}/quick-test-results",
            credential=credential,
            headers={"Idempotency-Key": idempotency_key},
            json=payload.model_dump(mode="json"),
        )
        return QuickTestResultReceiptPayload.model_validate(response.json())

    def create_artifact_upload(
        self,
        terminal_id: UUID,
        credential: str,
        payload: CreateArtifactUploadPayload,
        *,
        idempotency_key: str,
    ) -> ArtifactUploadPayload:
        response = self._request(
            "POST",
            "/api/v1/terminal/artifacts/uploads",
            credential=credential,
            headers={"Idempotency-Key": idempotency_key},
            json=payload.model_dump(mode="json"),
        )
        return ArtifactUploadPayload.model_validate(response.json())

    def upload_artifact(self, url: str, headers: dict[str, str], body: BinaryIO) -> None:
        try:
            response = self._client.put(url, headers=headers, content=body)
        except httpx.RequestError as exc:
            raise PlatformUnavailableError(
                0,
                "artifact_upload_unavailable",
                "artifact connection is unavailable",
            ) from exc
        if not response.is_success:
            raise PlatformError(
                response.status_code,
                "artifact_object_upload_failed",
                f"object store returned HTTP {response.status_code}",
            )

    def complete_artifact_upload(
        self, terminal_id: UUID, credential: str, artifact_id: UUID
    ) -> ArtifactUploadPayload:
        response = self._request(
            "POST",
            f"/api/v1/terminal/artifacts/uploads/{artifact_id}/complete",
            credential=credential,
            headers={"Prefer": "respond-async"},
        )
        return ArtifactUploadPayload.model_validate(response.json())

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        credential: str | None = None,
        json: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> httpx.Response:
        request_headers = dict(headers or {})
        if credential:
            request_headers["Authorization"] = f"Bearer {credential}"
        try:
            if max_bytes is not None:
                with self._client.stream(method, path, headers=request_headers) as streamed:
                    # Bound errors too, preserving typed credential failures without reading a Blob.
                    limit = max_bytes if streamed.is_success else 65536
                    content = bytearray()
                    for chunk in streamed.iter_bytes(65536):
                        if len(content) + len(chunk) > limit:
                            raise PlatformError(
                                502, "blob_range_invalid", "resource exceeded requested size"
                            )
                        content.extend(chunk)
                    response = httpx.Response(
                        streamed.status_code,
                        headers=streamed.headers,
                        content=bytes(content),
                        request=streamed.request,
                    )
            else:
                response = self._client.request(
                    method,
                    path,
                    headers=request_headers or None,
                    json=json,
                    params=params,
                )
        except httpx.RequestError as exc:
            raise PlatformUnavailableError(
                0,
                "platform_unavailable",
                "platform connection is unavailable",
            ) from exc
        if response.is_success:
            return response
        code = "platform_http_error"
        message = f"platform returned HTTP {response.status_code}"
        try:
            error_body = response.json()
            if isinstance(error_body, dict):
                top_level_code = error_body.get("code")
                top_level_message = error_body.get("message")
                if isinstance(top_level_code, str) and top_level_code:
                    code = top_level_code
                if isinstance(top_level_message, str) and top_level_message:
                    message = top_level_message
                detail = error_body.get("detail")
            else:
                detail = None
            if isinstance(detail, dict):
                code = str(detail.get("code") or code)
                message = str(detail.get("message") or message)
            elif isinstance(detail, str):
                message = detail
        except ValueError:
            pass
        error_type = (
            PlatformCredentialRejectedError
            if response.status_code == 401 and code == "invalid_terminal_credential"
            else PlatformError
        )
        raise error_type(response.status_code, code, message)
