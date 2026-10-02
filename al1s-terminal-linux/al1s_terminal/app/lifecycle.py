from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import NoReturn
from uuid import uuid4

from al1s_terminal.app.config import TerminalSettings
from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.providers.storage import runtime_storage
from al1s_terminal.providers.system import SystemCapability
from al1s_terminal.transport.models import (
    CapabilityProfilePayload,
    HeartbeatPayload,
    RegisterTerminalPayload,
)
from al1s_terminal.transport.platform import PlatformCredentialRejectedError, PlatformPort
from al1s_terminal.types import InstallationRecord, RegistrationStatus, SecureIdentity


class RegistrationRequiredError(RuntimeError):
    pass


class TerminalLifecycleService:
    def __init__(
        self,
        *,
        settings: TerminalSettings,
        platform: PlatformPort,
        secret_store: FileSecretStore,
        uow_factory: Callable[[], LocalUnitOfWork],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._platform = platform
        self._secret_store = secret_store
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def ensure_installation(self) -> InstallationRecord:
        now = self._clock()
        secure = self._secret_store.load()
        with self._uow_factory() as uow:
            installation = uow.installation.get()
            if installation is None:
                installation_id = secure.installation_id if secure is not None else uuid4()
                uow.installation.add_unregistered(
                    installation_id,
                    self._settings.agent_version,
                    now,
                )
                installation = uow.installation.get()
            assert installation is not None
        if secure is None:
            return installation
        if installation.installation_id != secure.installation_id:
            raise RuntimeError("terminal installation identity is inconsistent")
        if (
            installation.terminal_id == secure.terminal_id
            and installation.credential_epoch == secure.credential_epoch
        ):
            return installation
        with self._uow_factory() as uow:
            return uow.installation.mark_registered(
                terminal_id=secure.terminal_id,
                terminal_row_version=secure.terminal_row_version,
                credential_epoch=secure.credential_epoch,
                agent_version=self._settings.agent_version,
                now=now,
            )

    def register_if_needed(self) -> InstallationRecord:
        installation = self.ensure_installation()
        secure = self._secret_store.load()
        if (
            installation.registration_status is RegistrationStatus.REGISTERED
            and installation.terminal_id is not None
            and secure is not None
        ):
            return installation
        registration_code = self._settings.registration_code
        if registration_code is None:
            raise RegistrationRequiredError("a one-time registration code is required")
        registered = self._platform.register(
            RegisterTerminalPayload(
                registration_code=registration_code.get_secret_value(),
                installation_id=installation.installation_id,
                display_name=self._settings.display_name,
                agent_version=self._settings.agent_version,
            )
        )
        secure = SecureIdentity(
            installation_id=installation.installation_id,
            terminal_id=registered.terminal.terminal_id,
            credential=registered.credential,
            credential_epoch=installation.credential_epoch + 1,
            terminal_row_version=registered.terminal.row_version,
        )
        self._secret_store.save(secure)
        with self._uow_factory() as uow:
            return uow.installation.mark_registered(
                terminal_id=secure.terminal_id,
                terminal_row_version=secure.terminal_row_version,
                credential_epoch=secure.credential_epoch,
                agent_version=self._settings.agent_version,
                now=self._clock(),
            )

    def require_registered_identity(self) -> InstallationRecord:
        """Return the durable local identity that permits offline execution."""
        installation, _secure = self._require_registered()
        if installation.registration_status is not RegistrationStatus.REGISTERED:
            raise RegistrationRequiredError("terminal is not registered")
        return installation

    def heartbeat(self, *, accepting_tasks: bool = True) -> InstallationRecord:
        installation, secure = self._require_registered()
        if installation.terminal_row_version is None:
            raise RuntimeError("terminal platform row version is missing")
        try:
            terminal = self._platform.heartbeat(
                secure.terminal_id,
                secure.credential,
                HeartbeatPayload(
                    expected_version=installation.terminal_row_version,
                    acceptance_status="accepting" if accepting_tasks else "draining",
                    agent_version=self._settings.agent_version,
                    storage=runtime_storage(self._settings.data_dir),
                ),
            )
        except PlatformCredentialRejectedError as exc:
            self.handle_credential_rejection(exc)
        with self._uow_factory() as uow:
            return uow.installation.record_heartbeat(
                terminal_row_version=terminal.row_version,
                agent_version=self._settings.agent_version,
                now=self._clock(),
            )

    def publish_capability(self, capability: SystemCapability) -> InstallationRecord:
        installation, secure = self._require_registered()
        content = {
            "schema_version": 1,
            "protocol_version": 1,
            "agent_version": self._settings.agent_version,
            "os_name": capability.os_name,
            "os_version": capability.os_version,
            "architecture": capability.architecture,
            "cpu_cores": capability.cpu_cores,
            "memory_bytes": capability.memory_bytes,
            "storage_available_bytes": _storage_bucket(capability.storage_available_bytes),
            "accelerator_type": None,
            "low_resource": capability.low_resource,
            "provider_keys": list(capability.provider_keys),
            "details": capability.details,
        }
        local_hash = _canonical_hash(content)
        if installation.last_capability_hash == local_hash:
            return installation
        revision = installation.capability_revision + 1
        try:
            result = self._platform.publish_capability(
                secure.terminal_id,
                secure.credential,
                CapabilityProfilePayload.model_validate({"revision": revision, **content}),
            )
        except PlatformCredentialRejectedError as exc:
            self.handle_credential_rejection(exc)
        with self._uow_factory() as uow:
            return uow.installation.record_capability(
                revision=result.revision,
                manifest_hash=local_hash,
                now=self._clock(),
            )

    def _require_registered(self) -> tuple[InstallationRecord, SecureIdentity]:
        installation = self.ensure_installation()
        secure = self._secret_store.load()
        if installation.terminal_id is None or secure is None:
            raise RegistrationRequiredError("terminal is not registered")
        if installation.terminal_id != secure.terminal_id:
            raise RuntimeError("terminal identity is inconsistent")
        return installation, secure

    def handle_credential_rejection(self, error: PlatformCredentialRejectedError) -> NoReturn:
        self._mark_credential_rejected()
        raise RegistrationRequiredError(
            "terminal credential was rejected; a new registration code is required"
        ) from error

    def _mark_credential_rejected(self) -> None:
        with self._uow_factory() as uow:
            uow.installation.mark_credential_rejected(now=self._clock())


def _canonical_hash(value: dict[str, object]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _storage_bucket(value: int) -> int:
    bucket = 256 * 1024**2
    return max(0, value // bucket * bucket)
