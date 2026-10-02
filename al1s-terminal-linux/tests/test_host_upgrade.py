from threading import Event
from uuid import uuid4

import pytest

from al1s_terminal.host_management.journal import Journal
from al1s_terminal.host_management.service import (
    HostMaintenance,
    MaintenanceError,
    Observation,
    UpgradeRequest,
)


class Driver:
    def observe(self):
        return Observation("boot", "c" * 64, "start", True)


class Upgrade:
    def __init__(self):
        self.started, self.finish = Event(), Event()
        self.calls = 0
        self.outcome = None

    def run(self, _identity, _release, _started):
        self.calls += 1
        self.started.set()
        self.finish.wait(3)

    def result(self, _identity):
        return self.outcome


def test_upgrade_response_is_not_success_and_timeout_is_30_minutes(tmp_path):
    journal = Journal(tmp_path / "commands.db")
    upgrade, now = Upgrade(), [1000]
    service = HostMaintenance(journal, Driver(), clock=lambda: now[0], upgrade=upgrade)
    request = UpgradeRequest(
        command_id=uuid4(),
        action="upgrade_container",
        release_id=uuid4(),
        expires_at=1600,
        confirm_interrupt=True,
        expected_boot_id="boot",
        expected_container_id="c" * 64,
        expected_container_started_at="start",
    )
    try:
        assert service.submit(request)["state"] == "accepted"
        service.tick()
        assert upgrade.started.wait(1)
        assert service.submit(request)["state"] == "executing"
        assert upgrade.calls == 1
        now[0] = 1601
        service.tick()
        assert service.get(request.command_id)["state"] == "executing"
        now[0] = 2800
        service.tick()
        assert service.get(request.command_id)["state"] == "recovery_timeout"
        with pytest.raises(MaintenanceError, match="previous_upgrade_unresolved"):
            service.submit(request.model_copy(update={"command_id": uuid4(), "expires_at": 3000}))
        upgrade.outcome = "succeeded"
        upgrade.finish.set()
        service.upgrade_thread.join(2)
        late = service.get(request.command_id)
        assert late["state"] == "recovery_timeout" and late["late_state"] == "succeeded"
        assert upgrade.calls == 1
    finally:
        upgrade.finish.set()
        if service.upgrade_thread:
            service.upgrade_thread.join(2)
        journal.close()


def test_restart_of_daemon_reconciles_instead_of_reexecuting(tmp_path):
    journal = Journal(tmp_path / "commands.db")
    upgrade = Upgrade()
    service = HostMaintenance(journal, Driver(), clock=lambda: 1000, upgrade=upgrade)
    request = UpgradeRequest(
        command_id=uuid4(),
        action="upgrade_container",
        release_id=uuid4(),
        expires_at=1600,
        confirm_interrupt=True,
        expected_boot_id="boot",
        expected_container_id="c" * 64,
        expected_container_started_at="start",
    )
    try:
        service.submit(request)
        service.tick()
        assert upgrade.started.wait(1)
        upgrade.finish.set()
        service.upgrade_thread.join(2)
        upgrade.outcome = "failed"
        restarted = HostMaintenance(journal, Driver(), clock=lambda: 1010, upgrade=upgrade)
        restarted.tick()
        assert restarted.get(request.command_id)["state"] == "failed"
        assert upgrade.calls == 1
    finally:
        upgrade.finish.set()
        if service.upgrade_thread:
            service.upgrade_thread.join(2)
        journal.close()
