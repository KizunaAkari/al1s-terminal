import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest
from test_terminal_deployer import CANDIDATE_IMAGE, FakeRunner, _config

from al1s_terminal.persistence.migrations import upgrade_database
from terminal_deployer.models import DeploymentError
from terminal_deployer.recovery import SCHEMA_CHECK
from terminal_deployer.runner import CommandResult
from terminal_deployer.service import TerminalDeployer
from terminal_deployer.state import DeploymentStateStore


class RecoveryRunner(FakeRunner):
    helpers = ""
    schema_ok = True

    def _result(self, command, env):
        if command[:3] == ["docker", "ps", "-aq"]:
            return CommandResult(0, self.helpers)
        if command[:2] == ["docker", "run"] and "-c" in command:
            assert "--read-only" in command
            assert command[command.index("--network") + 1] == "none"
            assert any(value.endswith(":/data:ro") for value in command)
            compile(command[-1], "<schema-check>", "exec")
            return CommandResult(0 if self.schema_ok else 1,
                                 "AL1S_SCHEMA_OK" if self.schema_ok else "")
        return super()._result(command, env)


def setup_recovery(tmp_path, phase="candidate_starting", boot="new"):
    config = _config(tmp_path)
    states = DeploymentStateStore(config.state_dir)
    states.claim_upgrade("release-1")
    states.write("release-1", status=phase, host_boot_id="old", recovery_protocol=1,
                 live_migration_completed=True, image_id=CANDIDATE_IMAGE)
    states.write("release-1", status="recovery_required", error="deployment_execution_timeout")
    runner = RecoveryRunner(config)
    runner.running_image = CANDIDATE_IMAGE
    return TerminalDeployer(config, runner=runner, boot_id=lambda: boot), states, runner


def test_crash_after_candidate_start_recovers_without_redeploy_or_database_write(tmp_path):
    deployer, states, runner = setup_recovery(tmp_path)
    result = deployer.recover("release-1")
    assert result["status"] == "succeeded"
    assert states.unresolved_upgrade() is None
    assert states.read("release-1")["interrupted_status"] == "recovery_required"
    assert states.read("release-1")["error"] == "deployment_execution_timeout"
    count = len(runner.commands)
    assert deployer.recover("release-1")["resolved"] is True
    assert len(runner.commands) == count
    assert not any(command[:2] in (["docker", "compose"], ["docker", "load"])
                   for command, _ in runner.commands)


@pytest.mark.parametrize("phase", ["accepted", "verifying", "preflight"])
def test_crash_before_cutover_recovers_as_safe_failure(tmp_path, phase):
    deployer, states, _ = setup_recovery(tmp_path, phase=phase)
    assert deployer.recover("release-1")["status"] == "failed"
    assert states.unresolved_upgrade() is None


@pytest.mark.parametrize("case", ["same_boot", "helpers", "schema", "wrong_image"])
def test_missing_safety_evidence_does_not_clear_upgrade_lock(tmp_path, case):
    deployer, states, runner = setup_recovery(
        tmp_path, boot="old" if case == "same_boot" else "new",
    )
    if case == "helpers":
        runner.helpers = "orphan-helper"
    if case == "schema":
        runner.schema_ok = False
    if case == "wrong_image":
        runner.running_image = "sha256:" + "9" * 64
    assert deployer.recover("release-1")["resolved"] is False
    assert states.unresolved_upgrade() == "release-1"


def test_lost_migration_completion_record_uses_actual_schema_evidence(tmp_path):
    deployer, states, _ = setup_recovery(tmp_path, phase="cutover")
    states.write("release-1", live_migration_completed=False)
    assert deployer.recover("release-1")["status"] == "succeeded"
    assert states.unresolved_upgrade() is None


def test_healthy_previous_version_only_settles_after_schema_check(tmp_path):
    deployer, states, runner = setup_recovery(tmp_path, phase="cutover")
    previous = "sha256:" + "1" * 64
    states.write("release-1", previous_image_id=previous, live_migration_completed=False)
    runner.running_image = previous
    runner.schema_ok = False
    assert deployer.recover("release-1")["resolved"] is False
    runner.schema_ok = True
    assert deployer.recover("release-1")["status"] == "failed"
    assert states.read("release-1")["recovery_reason"] == "previous_version_verified"


def test_active_deployment_excludes_recovery(tmp_path):
    deployer, states, runner = setup_recovery(tmp_path)
    with deployer._deployment_lock(), pytest.raises(DeploymentError, match="already running"):
        deployer.recover("release-1")
    assert runner.commands == []
    assert states.unresolved_upgrade() == "release-1"


def test_schema_probe_is_readonly_source():
    compile(SCHEMA_CHECK, "<schema-check>", "exec")
    assert "mode=ro" in SCHEMA_CHECK
    assert "upgrade(" not in SCHEMA_CHECK


def test_schema_probe_checks_actual_database_without_modifying_it(tmp_path):
    database = tmp_path / "terminal.db"
    upgrade_database(f"sqlite:///{database.as_posix()}")
    source = SCHEMA_CHECK.replace(
        "file:/data/terminal.db?mode=ro", f"file:{database.as_posix()}?mode=ro",
    )
    before = database.read_bytes()
    result = subprocess.run([sys.executable, "-c", source], capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "AL1S_SCHEMA_OK"
    assert database.read_bytes() == before
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE alembic_version SET version_num = 'wrong-revision'")
        connection.commit()
    result = subprocess.run([sys.executable, "-c", source], capture_output=True,
                            text=True, timeout=30)
    assert result.returncode != 0
    assert "database_revision_unconfirmed" in result.stderr
