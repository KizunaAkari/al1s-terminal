from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class RegisterTerminalPayload(BaseModel):
    registration_code: str
    installation_id: UUID
    terminal_type: str = "linux"
    display_name: str
    agent_version: str


class TerminalPayload(BaseModel):
    terminal_id: UUID
    installation_id: UUID
    terminal_type: str
    display_name: str
    service_status: str
    acceptance_status: str
    agent_version: str
    current_capability_profile_id: UUID | None
    row_version: int
    created_at: datetime
    last_seen_at: datetime | None


class RegisteredTerminalPayload(BaseModel):
    terminal: TerminalPayload
    credential: str


class HeartbeatPayload(BaseModel):
    expected_version: int = Field(ge=1)
    service_status: str = "online"
    acceptance_status: str = "accepting"
    agent_version: str
    storage: dict[str, int | str] | None = None


class CapabilityProfilePayload(BaseModel):
    revision: int = Field(ge=1)
    schema_version: int = 1
    protocol_version: int = 1
    agent_version: str
    os_name: str
    os_version: str
    architecture: str
    cpu_cores: int
    memory_bytes: int
    storage_available_bytes: int
    accelerator_type: str | None = None
    low_resource: bool = False
    provider_keys: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class CapabilityProfileResult(BaseModel):
    profile_id: UUID
    terminal_id: UUID
    revision: int
    manifest_hash: str
    created_at: datetime


class DiscoverTargetIdentifierPayload(BaseModel):
    source_type: str = "adb_serial"
    identifier: str = Field(min_length=1, max_length=512)
    adb_state: str | None = None


class AdbObservationPayload(BaseModel):
    identifier_id: UUID
    adb_state: str


class AdbObservationBatchPayload(BaseModel):
    items: list[AdbObservationPayload] = Field(min_length=1, max_length=100)


class TargetIdentifierPayload(BaseModel):
    identifier_id: UUID
    target_device_id: UUID | None
    source_terminal_id: UUID
    source_type: str
    display_hint: str
    row_version: int = Field(ge=1)
    created_at: datetime
    bound_at: datetime | None
