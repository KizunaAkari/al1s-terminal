from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from types import TracebackType
from typing import Protocol

from al1s_terminal.execution.artifact_store import LocalArtifactStore
from al1s_terminal.execution.content_store import ContentAddressedStore
from al1s_terminal.gc_types import (
    ClaimedLocalGcJob,
    FailedLocalGcJob,
    LocalGcCycleResult,
    LocalGcTargetKind,
)


class LocalGcRepositoryPort(Protocol):
    def protect_deletions(
        self,
        jobs: Sequence[ClaimedLocalGcJob],
        *,
        worker_id: str,
        now: datetime,
    ) -> Sequence[ClaimedLocalGcJob]: ...

    def prepare_candidates(self, *, cutoff: datetime, now: datetime, limit: int) -> int: ...

    def claim_batch(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
        limit: int,
    ) -> Sequence[ClaimedLocalGcJob]: ...

    def settle(
        self,
        *,
        worker_id: str,
        succeeded: Sequence[ClaimedLocalGcJob],
        failed: Sequence[FailedLocalGcJob],
        now: datetime,
        max_attempts: int,
    ) -> tuple[int, int]: ...


class LocalGcUnitOfWork(Protocol):
    gc: LocalGcRepositoryPort

    def __enter__(self) -> LocalGcUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


class LocalGarbageCollector:
    def __init__(
        self,
        *,
        uow_factory: Callable[[], LocalGcUnitOfWork],
        artifact_store: LocalArtifactStore,
        content_store: ContentAddressedStore,
        retention: timedelta = timedelta(hours=24),
        batch_size: int = 20,
        worker_id: str = "terminal-local-gc",
        max_attempts: int = 8,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if retention < timedelta(minutes=5):
            raise ValueError("local GC retention must be at least five minutes")
        if not 1 <= batch_size <= 50:
            raise ValueError("local GC batch size must be between 1 and 50")
        self._uow_factory = uow_factory
        self._artifact_store = artifact_store
        self._content_store = content_store
        self._retention = retention
        self._batch_size = batch_size
        self._worker_id = worker_id
        self._max_attempts = max_attempts
        self._clock = clock or (lambda: datetime.now(UTC))

    def collect_once(self) -> LocalGcCycleResult:
        now = self._clock()
        with self._uow_factory() as uow:
            prepared = uow.gc.prepare_candidates(
                cutoff=now - self._retention,
                now=now,
                limit=self._batch_size,
            )
        with self._uow_factory() as uow:
            jobs = tuple(
                uow.gc.claim_batch(
                    worker_id=self._worker_id,
                    now=now,
                    lease_duration=timedelta(minutes=2),
                    limit=self._batch_size,
                )
            )
        succeeded: list[ClaimedLocalGcJob] = []
        failed: list[FailedLocalGcJob] = []
        with self._uow_factory() as uow:
            for job in uow.gc.protect_deletions(jobs, worker_id=self._worker_id, now=self._clock()):
                try:
                    self._delete_file(job)
                    succeeded.append(job)
                except (OSError, ValueError) as exc:
                    failed.append(FailedLocalGcJob(job, type(exc).__name__[:100]))
            deleted, failed_count = uow.gc.settle(
                worker_id=self._worker_id,
                succeeded=tuple(succeeded),
                failed=tuple(failed),
                now=self._clock(),
                max_attempts=self._max_attempts,
            )
        return LocalGcCycleResult(
            prepared=prepared,
            claimed=len(jobs),
            deleted=deleted,
            failed=failed_count,
        )

    def _delete_file(self, job: ClaimedLocalGcJob) -> None:
        if job.relative_path is None:
            return
        if job.target_kind is LocalGcTargetKind.ARTIFACT:
            self._artifact_store.delete(job.relative_path)
        elif job.target_kind is LocalGcTargetKind.WORK_ITEM:
            self._content_store.delete_definition(job.relative_path)
        else:
            self._content_store.delete_blob(job.relative_path)
