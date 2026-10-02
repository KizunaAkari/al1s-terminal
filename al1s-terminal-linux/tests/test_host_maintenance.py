from uuid import uuid4

import pytest

from al1s_terminal.host_management.journal import Command, Journal
from al1s_terminal.host_management.service import (
    CancelRequest,
    HostMaintenance,
    MaintenanceError,
    Observation,
    RecoveryRequest,
    RestartRequest,
)


class Driver:
    observation = Observation("boot-a", "c" * 64, "start-a", True)
    calls = 0
    starts = 0

    def observe(self):
        return self.observation

    def logs(self):
        return {"observed_at": "2026-09-25T00:00:00Z", "lines": ["safe event"]}

    def execute(self, _action, _container):
        self.calls += 1

    def restore_container(self, _container):
        self.starts += 1


@pytest.fixture
def rig(tmp_path):
    journal = Journal(tmp_path / "maintenance.db")
    driver = Driver()
    now = [1000.0]
    service = HostMaintenance(journal, driver, lambda: now[0])
    request = RestartRequest(
        command_id=uuid4(),
        action="restart_container",
        expires_at=1500,
        confirm_interrupt=True,
        expected_boot_id="boot-a",
        expected_container_id="c" * 64,
        expected_container_started_at="start-a",
    )
    yield service, driver, journal, now, request
    journal.close()


def test_duplicate_command_runs_once_and_requires_new_instance_evidence(rig):
    service, driver, _, _, request = rig
    assert service.submit(request)["state"] == "accepted"
    service.tick()
    assert service.submit(request)["state"] == "recovering"
    service.tick()
    assert service.get(request.command_id)["state"] == "recovering"
    driver.observation = Observation("boot-a", "c" * 64, "start-b", True)
    service.tick()
    assert service.get(request.command_id)["state"] == "succeeded"
    assert driver.calls == 1


def test_expired_command_never_executes(rig):
    service, driver, _, now, request = rig
    service.submit(request)
    now[0] = 1500
    service.tick()
    assert driver.calls == 0
    assert service.get(request.command_id)["state"] == "expired"


def test_cancel_waiting_command_is_durable_and_never_executes(rig):
    service, driver, _, _, request = rig
    original = service.submit(request)
    body = CancelRequest(command_id=request.command_id, version=original["version"])
    result = service.cancel(body)
    assert result["state"] == "cancelled"
    assert service.cancel(body) == result
    service.tick()
    assert driver.calls == 0
    assert service.get(request.command_id)["state"] == "cancelled"


def test_cancel_cannot_interrupt_started_command(rig):
    service, driver, _, _, request = rig
    original = service.submit(request)
    service.tick()
    with pytest.raises(MaintenanceError, match="command_not_cancellable"):
        service.cancel(CancelRequest(command_id=request.command_id, version=original["version"]))
    assert driver.calls == 1


@pytest.mark.parametrize("cancel_first", [True, False])
def test_download_cancel_and_install_seal_are_serialized(rig, cancel_first):
    from al1s_terminal.host_management.upgrades import UpgradeCancelled
    service, driver, journal, _, request = rig
    service.submit(request)
    with journal.transaction() as session, session.begin():
        command = session.get(Command, str(request.command_id))
        command.action, command.state = "upgrade_container", "executing"
        command.error_code = "upgrade_downloading"
    if cancel_first:
        result = service.cancel(CancelRequest(command_id=request.command_id, version=1))
        assert result["state"] == "executing"
        assert result["error_code"] == "upgrade_cancel_requested"
        with pytest.raises(UpgradeCancelled):
            service._upgrade_checkpoint(str(request.command_id), seal=True)
    else:
        service._upgrade_checkpoint(str(request.command_id), seal=True)
        with pytest.raises(MaintenanceError):
            service.cancel(CancelRequest(command_id=request.command_id, version=2))
        assert service.get(request.command_id)["error_code"] == "upgrade_installing"
    assert driver.calls == 0


def test_recovery_is_mutually_exclusive_with_maintenance_and_does_not_restart(rig):
    service, driver, _, _, request = rig
    calls = []
    service.recovery = lambda identity: calls.append(identity) or {
        "deployment_id": identity, "resolved": False,
    }
    assert service.recover(RecoveryRequest(deployment_id="release-1"))["resolved"] is False
    assert calls == ["release-1"]
    assert driver.calls == 0
    service.submit(request)
    with pytest.raises(MaintenanceError, match="maintenance_in_progress"):
        service.recover(RecoveryRequest(deployment_id="release-1"))
    assert calls == ["release-1"]


def test_automatic_recovery_queries_original_upgrade(rig):
    service, driver, _, _, _ = rig
    calls = []
    service.recovery = lambda identity: calls.append(identity) or {
        "deployment_id": identity, "resolved": False,
    }
    driver.observation = Observation("boot", "c", "start", True, "release-1")
    service.recover_pending()
    assert calls == ["release-1"]
    assert driver.calls == 0


@pytest.mark.parametrize("action", ["restart_container", "restart_host"])
def test_unknown_upgrade_requires_separate_confirmation_and_survives_restart(rig, action):
    service, driver, journal, _, original = rig
    request = original.model_copy(update={"action": action})
    driver.observation = Observation("boot-a", "c" * 64, "start-a", True, "release-1")
    with pytest.raises(MaintenanceError, match="upgrade_risk_confirmation_required"):
        service.submit(request)
    confirmed = request.model_copy(update={"confirmed_upgrade_id": "release-1"})
    assert service.submit(confirmed)["state"] == "accepted"
    service.tick()
    assert driver.calls == 1
    driver.observation = Observation(
        "boot-b" if action == "restart_host" else "boot-a",
        "c" * 64,
        "start-b",
        True,
        "release-1",
    )
    service.tick()
    assert service.get(confirmed.command_id)["state"] == "succeeded"
    assert driver.observe().unresolved_upgrade_id == "release-1"
    with journal.transaction() as session:
        assert session.get(Command, str(confirmed.command_id)).confirmed_upgrade_id == "release-1"


def test_changed_upgrade_risk_before_execution_refuses_old_confirmation(rig):
    service, driver, _, _, request = rig
    service.submit(request)
    driver.observation = Observation("boot-a", "c" * 64, "start-a", True, "new-upgrade")
    service.tick()
    assert driver.calls == 0
    assert service.get(request.command_id)["error_code"] == "upgrade_risk_changed"


@pytest.mark.parametrize("action", ["restart_container", "restart_host"])
@pytest.mark.parametrize("before_accept", [True, False])
def test_changed_container_start_invalidates_confirmation(rig, action, before_accept):
    service, driver, _, _, original = rig
    request = original.model_copy(update={"action": action})
    if not before_accept:
        service.submit(request)
    driver.observation = Observation("boot-a", "c" * 64, "new-start", True)
    if before_accept:
        with pytest.raises(MaintenanceError, match="host_identity_changed"):
            service.submit(request)
    else:
        service.tick()
        assert service.get(request.command_id)["state"] == "refused"
    assert driver.calls == 0


def test_versioned_journal_reopen_preserves_accepted_command(tmp_path):
    from sqlalchemy import text

    path = tmp_path / "commands.db"
    journal = Journal(path)
    request = RestartRequest(
        command_id=uuid4(),
        action="restart_container",
        expires_at=1500,
        confirm_interrupt=True,
        expected_boot_id="boot-a",
        expected_container_id="c" * 64,
        expected_container_started_at="start-a",
    )
    HostMaintenance(journal, Driver(), lambda: 1000).submit(request)
    journal.close()
    reopened = Journal(path)
    try:
        with reopened.transaction() as session:
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == "host_0003"
        assert (
            HostMaintenance(reopened, Driver(), lambda: 1000).get(request.command_id)["state"]
            == "accepted"
        )
    finally:
        reopened.close()


def test_crash_after_execution_claim_never_replays(rig):
    service, driver, journal, now, request = rig
    service.submit(request)
    with journal.transaction() as session, session.begin():
        command = session.get(Command, str(request.command_id))
        command.state, command.started_at = "executing", 1000
    recovered = HostMaintenance(journal, driver, lambda: now[0])
    recovered.tick()
    now[0] = 1600
    recovered.tick()
    assert driver.calls == 0
    assert recovered.get(request.command_id)["state"] == "recovery_timeout"


def test_same_id_different_action_conflicts(rig):
    service, _, _, _, request = rig
    service.submit(request)
    with pytest.raises(MaintenanceError, match="identity_conflict"):
        service.submit(request.model_copy(update={"action": "restart_host"}))


def test_host_restart_needs_new_boot_and_health(rig):
    service, driver, _, _, request = rig
    request = request.model_copy(update={"action": "restart_host"})
    service.submit(request)
    service.tick()
    driver.observation = Observation("boot-a", "c" * 64, "new-start", True)
    service.tick()
    assert service.get(request.command_id)["state"] == "recovering"
    driver.observation = Observation("boot-b", "c" * 64, "new-start", False)
    service.tick()
    assert service.get(request.command_id)["state"] == "recovering"
    driver.observation = Observation("boot-b", "c" * 64, "new-start", True)
    service.tick()
    assert service.get(request.command_id)["state"] == "succeeded"
    assert driver.starts == 1


def test_host_recovery_start_is_claimed_once_and_never_targets_replacement(rig):
    service, driver, journal, now, request = rig
    request = request.model_copy(update={"action": "restart_host"})
    service.submit(request)
    service.tick()
    driver.observation = Observation("boot-b", "d" * 64, "new-start", False)
    service.tick()
    assert driver.starts == 0
    driver.observation = Observation("boot-b", "c" * 64, "new-start", False)
    service.tick()
    HostMaintenance(journal, driver, lambda: now[0]).tick()
    assert driver.starts == 1 and driver.calls == 1


def test_target_change_before_execution_is_refused(rig):
    service, driver, _, _, request = rig
    service.submit(request)
    driver.observation = Observation("other-boot", "c" * 64, "start-a", True)
    service.tick()
    assert driver.calls == 0
    assert service.get(request.command_id)["state"] == "refused"


def test_recovery_deadline_is_not_reset_by_late_health(rig):
    service, driver, _, now, request = rig
    service.submit(request)
    service.tick()
    now[0] = 1600
    driver.observation = Observation("boot-a", "c" * 64, "new-start", True)
    service.tick()
    assert service.get(request.command_id)["state"] == "recovery_timeout"
    assert service.get(request.command_id)["late_state"] == "succeeded"


def test_host_restart_remains_available_without_docker(rig):
    service, driver, _, _, request = rig
    driver.observation = Observation("boot-a", "", "", False)
    request = request.model_copy(
        update={
            "expected_container_id": "",
            "expected_container_started_at": "",
        }
    )
    with pytest.raises(MaintenanceError, match="container_unavailable"):
        service.submit(request)
    request = request.model_copy(update={"action": "restart_host"})
    service.submit(request)
    service.tick()
    assert driver.calls == 1


def test_http_auth_and_command_acceptance(rig):
    import json
    from threading import Thread
    from urllib.error import HTTPError
    from urllib.request import ProxyHandler, Request, build_opener

    from al1s_terminal.host_management.http_api import create_server

    service, driver, _, _, request = rig
    server = create_server(("127.0.0.1", 0), service, "x" * 32)
    thread = Thread(target=server.serve_forever)
    thread.start()
    opener = build_opener(ProxyHandler({}))
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(HTTPError) as rejected:
            opener.open(origin + "/v1/health", timeout=3)
        assert rejected.value.code == 401
        rejected.value.close()
        log_request = Request(origin + "/v1/logs", headers={
            "Authorization": "Bearer " + "x" * 32,
        })
        with opener.open(log_request, timeout=3) as response:
            assert response.headers["Cache-Control"] == "no-store"
            assert json.load(response)["lines"] == ["safe event"]
        message = Request(
            origin + "/v1/commands",
            data=request.model_dump_json().encode(),
            headers={"Authorization": "Bearer " + "x" * 32},
            method="POST",
        )
        with opener.open(message, timeout=3) as response:
            assert response.status == 202
            assert json.load(response)["state"] == "accepted"
        assert driver.calls == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_container_log_view_is_fixed_bounded_and_redacts_credentials(monkeypatch, tmp_path):
    import base64
    import json
    import subprocess
    from datetime import UTC, datetime, timedelta

    from al1s_terminal.host_management.driver import LinuxHostDriver

    def no_docker(*_args, **_kwargs):
        raise AssertionError("log view must not invoke Docker")

    monkeypatch.setattr(subprocess, "run", no_docker)
    now = datetime.now(UTC)
    for age, container, message in (
        (timedelta(days=8), "al1s-terminal-linux", "expired"),
        (timedelta(minutes=2), "unrelated-container", "other"),
        (timedelta(minutes=1), "al1s-terminal-linux", "safe event"),
        (timedelta(seconds=1), "al1s-terminal-linux", "Authorization: Bearer secret"),
    ):
        received = now - age
        payload = f"<30>1 {received.isoformat()} host {container} 1 id - {message}\n".encode()
        record = {"occurred_at": received.isoformat(),
                  "syslog_b64": base64.b64encode(payload).decode()}
        path = tmp_path / f"{received:%Y%m%d%H}.jsonl"
        with path.open("a") as output:
            output.write(json.dumps(record) + "\n")
    lines = LinuxHostDriver("al1s-terminal-linux", log_directory=tmp_path).logs()["lines"]
    assert len(lines) == 2
    assert lines[0].endswith("safe event")
    assert lines[1] == "[redacted]"


def test_log_view_drops_partial_first_line_at_byte_limit():
    from al1s_terminal.host_management.driver import _safe_log_lines

    raw = b"Authorization: abc" + b"x" * 12_000 + b"\nvisible\n"
    assert _safe_log_lines(raw) == ["visible"]
