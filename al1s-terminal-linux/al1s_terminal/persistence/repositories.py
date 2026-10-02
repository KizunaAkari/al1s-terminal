"""Compatibility imports for the focused terminal persistence modules."""

from al1s_terminal.persistence.cached_resource_repository import (
    CachedResourceRepository as CachedResourceRepository,
)
from al1s_terminal.persistence.installation_repository import (
    InstallationRepository as InstallationRepository,
)
from al1s_terminal.persistence.local_artifact_repository import (
    LocalArtifactRepository as LocalArtifactRepository,
)
from al1s_terminal.persistence.offline_start_permit_repository import (
    OfflineStartPermitRepository as OfflineStartPermitRepository,
)
from al1s_terminal.persistence.outbox_repository import OutboxRepository as OutboxRepository
from al1s_terminal.persistence.package_manifest_repository import (
    PackageManifestRepository as PackageManifestRepository,
)
from al1s_terminal.persistence.target_device_observation_repository import (
    TargetDeviceObservationRepository as TargetDeviceObservationRepository,
)
from al1s_terminal.persistence.work_item_repository import WorkItemRepository as WorkItemRepository
