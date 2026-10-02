from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, TypeAdapter


class EditorRequest(BaseModel):
    session_id: UUID
    terminal_id: UUID
    device_id: UUID
    status: Literal["pending", "active", "closing", "closed", "failed", "expired"]
    create_deadline: datetime


class RequestSender(Protocol):
    def __call__(
        self,
        method: str,
        path: str,
        *,
        credential: str | None = None,
        json: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response: ...


class EditorPlatformClient:
    def __init__(self, request: RequestSender) -> None:
        self._request = request

    def pending(self, credential: str) -> list[EditorRequest]:
        response = self._request(
            "GET", "/api/v1/terminal/editor-sessions", credential=credential, params={"limit": "50"}
        )
        return TypeAdapter(list[EditorRequest]).validate_python(response.json())

    def report(
        self,
        credential: str,
        request: EditorRequest,
        instance: UUID,
        status: str,
        connection: dict[str, str] | None = None,
    ) -> EditorRequest:
        body: dict[str, object] = {
            "instance_id": str(instance),
            "status": status,
            "connection": connection,
        }
        response = self._request(
            "POST",
            f"/api/v1/terminal/editor-sessions/{request.session_id}/report",
            credential=credential,
            json=body,
        )
        return EditorRequest.model_validate(response.json())
