from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class LocalGcTargetKind(StrEnum):
    ARTIFACT = "artifact"
    WORK_ITEM = "work_item"
    RESOURCE = "resource"


@dataclass(frozen=True, slots=True)
class ClaimedLocalGcJob:
    job_id: UUID
    target_kind: LocalGcTargetKind
    target_id: str
    relative_path: str | None
    attempt_count: int
    row_version: int


@dataclass(frozen=True, slots=True)
class FailedLocalGcJob:
    job: ClaimedLocalGcJob
    error_code: str


@dataclass(frozen=True, slots=True)
class LocalGcCycleResult:
    prepared: int
    claimed: int
    deleted: int
    failed: int
