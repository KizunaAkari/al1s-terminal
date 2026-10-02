from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from threading import Lock, Thread
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from al1s_terminal.host_management.journal import Command, Journal, utc_timestamp


class MaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: UUID
    action: Literal["restart_container", "restart_host", "upgrade_container"]
    expires_at: float = Field(allow_inf_nan=False, gt=0, le=253402300799)
    confirm_interrupt: Literal[True]
    expected_boot_id: str = Field(min_length=1, max_length=64)
    expected_container_id: str = Field(max_length=64)
    expected_container_started_at: str = Field(max_length=64)
    confirmed_upgrade_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$",
    )


class RestartRequest(MaintenanceRequest):
    action: Literal["restart_container", "restart_host"]


class UpgradeRequest(MaintenanceRequest):
    action: Literal["upgrade_container"]
    release_id: UUID


class RecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deployment_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: UUID
    version: int = Field(ge=1)


@dataclass(frozen=True)
class Observation:
    boot_id: str
    container_id: str
    container_started_at: str
    healthy: bool
    unresolved_upgrade_id: str | None = None
    upgrade_in_progress: bool = False


class HostDriver(Protocol):
    def observe(self) -> Observation: ...
    def logs(self) -> dict[str, object]: ...
    def execute(self, action: str, container_id: str) -> None: ...
    def restore_container(self, container_id: str) -> None: ...


class MaintenanceError(RuntimeError):
    pass


class UpgradeExecutor(Protocol):
    def run(self, identity: str, release_id: UUID, started_at: float) -> None: ...
    def result(self, identity: str) -> str | None: ...


class HostMaintenance:
    """One daemon process owns the journal; its service-level lock serializes commands."""

    def __init__(
        self,
        journal: Journal,
        driver: HostDriver,
        clock: Callable[[], float] = utc_timestamp,
        recovery: Callable[[str], dict[str, object]] | None = None,
        upgrade: UpgradeExecutor | None = None,
    ):
        self.journal, self.driver, self.clock = journal, driver, clock
        self.lock = Lock()
        self.recovery = recovery
        self.upgrade = upgrade
        self.upgrade_thread: Thread | None = None

    def recover(self, request: RecoveryRequest) -> dict[str, object]:
        if self.recovery is None:
            raise MaintenanceError("upgrade_recovery_not_configured")
        if not self.lock.acquire(blocking=False):
            raise MaintenanceError("maintenance_in_progress")
        try:
            with self.journal.transaction() as session:
                if self.journal.active(session):
                    raise MaintenanceError("maintenance_in_progress")
            return self.recovery(request.deployment_id)
        finally:
            self.lock.release()

    def cancel(self, request: CancelRequest) -> dict[str, object]:
        # Serializes with tick's accepted -> executing commit, never kills a deployer.
        with self.lock, self.journal.transaction() as session, session.begin():
            command = session.get(Command, str(request.command_id))
            if command is None:
                raise MaintenanceError("command_not_found")
            if command.state == "cancelled":
                return self.summary(command)
            if command.version != request.version:
                raise MaintenanceError("command_not_cancellable")
            if (
                command.state == "executing"
                and command.action == "upgrade_container"
                and command.error_code in {"upgrade_downloading", "upgrade_cancel_requested"}
            ):
                command.error_code = "upgrade_cancel_requested"
                command.version += 1
                session.flush()
                return self.summary(command)
            if command.state != "accepted":
                raise MaintenanceError("command_not_cancellable")
            command.state, command.error_code = "cancelled", "cancelled_by_user"
            command.version += 1
            session.flush()
            return self.summary(command)

    def recover_pending(self) -> None:
        observed = self.driver.observe()
        if observed.unresolved_upgrade_id and not observed.upgrade_in_progress:
            self.recover(RecoveryRequest(deployment_id=observed.unresolved_upgrade_id))

    def submit(self, request: RestartRequest | UpgradeRequest) -> dict[str, object]:
        digest = hashlib.sha256(request.model_dump_json(exclude_none=True).encode()).hexdigest()
        with self.lock:
            with self.journal.transaction() as session:
                previous = session.get(Command, str(request.command_id))
                if previous:
                    if previous.request_hash != digest:
                        raise MaintenanceError("command_identity_conflict")
                    return self.summary(previous)
            observed = self.driver.observe()
            if isinstance(request, UpgradeRequest):
                if self.upgrade is None:
                    raise MaintenanceError("upgrade_not_configured")
                if observed.unresolved_upgrade_id or (
                    self.upgrade_thread and self.upgrade_thread.is_alive()
                ):
                    raise MaintenanceError("previous_upgrade_unresolved")
            if observed.upgrade_in_progress:
                raise MaintenanceError("upgrade_in_progress")
            if (
                observed.boot_id != request.expected_boot_id
                or observed.container_id != request.expected_container_id
                or observed.container_started_at != request.expected_container_started_at
            ):
                raise MaintenanceError("host_identity_changed")
            if request.action == "restart_container" and not observed.container_id:
                raise MaintenanceError("container_unavailable")
            if observed.unresolved_upgrade_id != request.confirmed_upgrade_id:
                raise MaintenanceError("upgrade_risk_confirmation_required")
            with self.journal.transaction() as session, session.begin():
                now = self.clock()
                if not now < request.expires_at <= now + 600:
                    raise MaintenanceError("command_expired_or_invalid_deadline")
                if self.journal.active(session):
                    raise MaintenanceError("maintenance_in_progress")
                command = Command(
                    command_id=str(request.command_id),
                    action=request.action,
                    request_hash=digest,
                    state="accepted",
                    expires_at=request.expires_at,
                    accepted_at=now,
                    previous_boot=observed.boot_id,
                    previous_container=observed.container_id,
                    previous_start=observed.container_started_at,
                    version=1,
                    confirmed_upgrade_id=request.confirmed_upgrade_id,
                    release_id=str(request.release_id)
                    if isinstance(request, UpgradeRequest)
                    else None,
                )
                session.add(command)
                session.flush()
                return self.summary(command)

    def get(self, command_id: UUID) -> dict[str, object] | None:
        with self.journal.transaction() as session:
            command = session.get(Command, str(command_id))
            if command is None:
                return None
            if command.state != "recovery_timeout" or command.late_state:
                return self.summary(command)
            if command.action == "upgrade_container":
                if self.upgrade and not (self.upgrade_thread and self.upgrade_thread.is_alive()):
                    late = self.upgrade.result(command.command_id)
                    if late:
                        command.late_state = late
                        command.version += 1
                        session.commit()
                return self.summary(command)
        try:
            observed = self.driver.observe()
        except Exception:
            observed = None
        with self.journal.transaction() as session, session.begin():
            command = session.get(Command, str(command_id))
            if command and observed and self._recovered(command, observed):
                command.late_state = "succeeded"
            return self.summary(command) if command else None

    def tick(self) -> None:
        with self.lock:
            with self.journal.transaction() as session:
                if self.journal.active(session) is None:
                    return
            try:
                observed = self.driver.observe()
            except Exception:
                observed = None
            if self._restore_after_boot(observed):
                return
            with self.journal.transaction() as session, session.begin():
                command = self.journal.active(session)
                if command is None:
                    return
                if command.state != "accepted":
                    if command.action == "upgrade_container":
                        self._reconcile_upgrade(command)
                    else:
                        self._reconcile(command, observed)
                    return
                if self.clock() >= command.expires_at:
                    command.state, command.error_code = "expired", "command_expired"
                    command.version += 1
                    return
                if observed is None:
                    return
                if observed.upgrade_in_progress:
                    command.state, command.error_code = "refused", "upgrade_in_progress"
                    command.version += 1
                    return
                if (
                    observed.boot_id != command.previous_boot
                    or observed.container_id != command.previous_container
                    or observed.container_started_at != command.previous_start
                ):
                    command.state, command.error_code = "refused", "target_changed"
                    command.version += 1
                    return
                if observed.unresolved_upgrade_id != command.confirmed_upgrade_id:
                    command.state, command.error_code = "refused", "upgrade_risk_changed"
                    command.version += 1
                    return
                command.state, command.started_at = "executing", self.clock()
                if command.action == "upgrade_container" and hasattr(
                    self.upgrade, "run_cancellable"
                ):
                    command.error_code = "upgrade_downloading"
                command.version += 1
                # Commit before invoking any disruptive operation. Crash recovery never replays it.
            if command.action == "upgrade_container":
                self.upgrade_thread = Thread(
                    target=self._execute_upgrade, args=(command,), daemon=True
                )
                self.upgrade_thread.start()
                return
            try:
                self.driver.execute(command.action, observed.container_id)
                code = None
            except Exception:
                # A timeout/connection loss does not prove the reboot did not happen.
                code = "execution_result_unknown"
            with self.journal.transaction() as session, session.begin():
                current = session.get(Command, command.command_id)
                if current is not None:
                    current.state, current.error_code = "recovering", code
                    current.version += 1

    def _execute_upgrade(self, command: Command) -> None:
        # Do not hold the management lock for a 30-minute deployment. Status and
        # risk-confirmed recovery must remain accessible if a subprocess stalls.
        if self.upgrade and command.release_id and command.started_at is not None:
            with suppress(Exception):
                cancellable = getattr(self.upgrade, "run_cancellable", None)
                if callable(cancellable):
                    cancellable(
                        command.command_id,
                        UUID(command.release_id),
                        command.started_at,
                        lambda: self._upgrade_checkpoint(command.command_id),
                        lambda: self._upgrade_checkpoint(command.command_id, seal=True),
                    )
                else:
                    self.upgrade.run(
                        command.command_id, UUID(command.release_id), command.started_at
                    )

    def _upgrade_checkpoint(self, identity: str, *, seal: bool = False) -> None:
        from al1s_terminal.host_management.upgrades import UpgradeCancelled

        with self.lock, self.journal.transaction() as session, session.begin():
            command = session.get(Command, identity)
            if command is None or command.state != "executing":
                raise MaintenanceError("upgrade_no_longer_executing")
            if command.error_code == "upgrade_cancel_requested":
                raise UpgradeCancelled()
            if seal:
                command.error_code = "upgrade_installing"
                command.version += 1

    def _reconcile_upgrade(self, command: Command) -> None:
        if command.started_at is None:
            return
        running = self.upgrade_thread and self.upgrade_thread.is_alive()
        result = self.upgrade.result(command.command_id) if self.upgrade and not running else None
        if self.clock() >= command.started_at + 1800:
            command.state, command.error_code = "recovery_timeout", "upgrade_result_unknown"
            command.late_state = result
        elif result:
            command.state = result
            command.error_code = "upgrade_failed" if result == "failed" else None
        else:
            return
        command.version += 1

    def _restore_after_boot(self, observed: Observation | None) -> bool:
        if observed is None:
            return False
        with self.journal.transaction() as session, session.begin():
            command = self.journal.active(session)
            if (
                command is None
                or command.action != "restart_host"
                or command.state not in {"executing", "recovering"}
                or command.started_at is None
                or self.clock() >= command.started_at + 600
                or observed.boot_id == command.previous_boot
                or observed.healthy
                or not command.previous_container
                or observed.container_id != command.previous_container
                or command.recovery_start_at is not None
            ):
                return False
            # One recovery start of the exact original container, never another reboot.
            # Persist first: a daemon crash cannot replay the disruptive command.
            command.recovery_start_at = self.clock()
            command.version += 1
            container_id = command.previous_container
        # Keep recovery pending on failure; only fresh health can prove recovery.
        with suppress(Exception):
            self.driver.restore_container(container_id)
        return True

    def _reconcile(self, command: Command, current: Observation | None) -> None:
        if command.started_at is not None and self.clock() >= command.started_at + 600:
            command.state, command.error_code = "recovery_timeout", "restart_recovery_timeout"
        elif current and self._recovered(command, current):
            command.state, command.error_code = "succeeded", None
        command.version += 1

    @staticmethod
    def _recovered(command: Command, current: Observation) -> bool:
        changed = (
            current.boot_id != command.previous_boot
            if command.action == "restart_host"
            else current.container_id == command.previous_container
            and current.container_started_at != command.previous_start
        )
        return changed and current.healthy

    @staticmethod
    def summary(command: Command) -> dict[str, object]:
        return {
            key: getattr(command, key)
            for key in (
                "command_id",
                "action",
                "state",
                "accepted_at",
                "started_at",
                "expires_at",
                "error_code",
                "version",
                "late_state",
                "release_id",
            )
        }
