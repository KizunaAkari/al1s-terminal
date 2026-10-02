from __future__ import annotations

from typing import BinaryIO, Protocol
from uuid import UUID

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


class DeliveryPlatformPort(Protocol):
    def issue_mqtt_session(
        self, terminal_id: UUID, credential: str
    ) -> TerminalMqttSessionPayload: ...

    def list_commands(
        self, terminal_id: UUID, credential: str, *, limit: int
    ) -> TerminalCommandPagePayload: ...

    def acknowledge_command(
        self,
        terminal_id: UUID,
        credential: str,
        command_id: UUID,
        payload: CommandAcknowledgementPayload,
    ) -> CommandAcknowledgementResultPayload: ...

    def get_task_package(
        self, terminal_id: UUID, credential: str, package_id: UUID
    ) -> TaskPackagePayload: ...

    def head_blob(
        self, terminal_id: UUID, credential: str, blob_id: UUID
    ) -> TerminalBlobMetadataPayload: ...

    def download_blob_range(
        self,
        terminal_id: UUID,
        credential: str,
        blob_id: UUID,
        *,
        start: int,
        end_inclusive: int,
    ) -> bytes: ...

    def submit_package_receipt(
        self,
        terminal_id: UUID,
        credential: str,
        package_id: UUID,
        payload: PackageReceiptPayload,
    ) -> PackageReceiptResultPayload: ...

    def start_attempt(
        self,
        terminal_id: UUID,
        credential: str,
        attempt_id: UUID,
        payload: AttemptStartPayload,
    ) -> AttemptStartResultPayload: ...

    def submit_attempt_result(
        self,
        terminal_id: UUID,
        credential: str,
        attempt_id: UUID,
        payload: AttemptResultPayload,
    ) -> AttemptResultReceiptPayload: ...

    def list_quick_tests(
        self, terminal_id: UUID, credential: str, *, limit: int
    ) -> QuickTestPagePayload: ...

    def claim_quick_test(
        self, terminal_id: UUID, credential: str, session_id: UUID
    ) -> QuickTestClaimPayload: ...

    def get_quick_test_control(
        self, terminal_id: UUID, credential: str, session_id: UUID
    ) -> QuickTestControlPayload: ...

    def report_quick_test_events(
        self, terminal_id: UUID, credential: str, session_id: UUID,
        payload: QuickTestEventBatchPayload,
    ) -> QuickTestEventReceiptPayload: ...

    def submit_quick_test_result(
        self,
        terminal_id: UUID,
        credential: str,
        script_id: UUID,
        payload: QuickTestResultPayload,
        *,
        idempotency_key: str,
    ) -> QuickTestResultReceiptPayload: ...

    def create_artifact_upload(
        self,
        terminal_id: UUID,
        credential: str,
        payload: CreateArtifactUploadPayload,
        *,
        idempotency_key: str,
    ) -> ArtifactUploadPayload: ...

    def upload_artifact(self, url: str, headers: dict[str, str], body: BinaryIO) -> None: ...

    def complete_artifact_upload(
        self, terminal_id: UUID, credential: str, artifact_id: UUID
    ) -> ArtifactUploadPayload: ...
