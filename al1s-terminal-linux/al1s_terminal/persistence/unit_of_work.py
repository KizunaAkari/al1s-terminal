from __future__ import annotations

from types import TracebackType

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from al1s_terminal.persistence.gc_repository import LocalGcRepository
from al1s_terminal.persistence.quick_test_events import QuickTestEventRepository
from al1s_terminal.persistence.repositories import (
    CachedResourceRepository,
    InstallationRepository,
    LocalArtifactRepository,
    OfflineStartPermitRepository,
    OutboxRepository,
    PackageManifestRepository,
    TargetDeviceObservationRepository,
    WorkItemRepository,
)


class LocalUnitOfWork:
    """Terminal transaction: normal exit commits, exceptional exit rolls back.

    A caught exception is a normal exit to this context manager. Let failures
    escape the block when pending writes must be discarded.
    """

    installation: InstallationRepository
    work_items: WorkItemRepository
    outbox: OutboxRepository
    packages: PackageManifestRepository
    resources: CachedResourceRepository
    target_devices: TargetDeviceObservationRepository
    artifacts: LocalArtifactRepository
    offline_permits: OfflineStartPermitRepository
    gc: LocalGcRepository
    quick_test_events: QuickTestEventRepository

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    @classmethod
    def from_engine(cls, engine: Engine) -> LocalUnitOfWork:
        return cls(sessionmaker(engine, expire_on_commit=False))

    def __enter__(self) -> LocalUnitOfWork:
        self._session = self._session_factory()
        self.installation = InstallationRepository(self._session)
        self.work_items = WorkItemRepository(self._session)
        self.outbox = OutboxRepository(self._session)
        self.packages = PackageManifestRepository(self._session)
        self.resources = CachedResourceRepository(self._session)
        self.target_devices = TargetDeviceObservationRepository(self._session)
        self.artifacts = LocalArtifactRepository(self._session)
        self.offline_permits = OfflineStartPermitRepository(self._session)
        self.gc = LocalGcRepository(self._session)
        self.quick_test_events = QuickTestEventRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        assert self._session is not None
        try:
            if exc_type is None:
                self._session.commit()
            else:
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None
