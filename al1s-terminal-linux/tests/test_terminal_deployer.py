from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import zipfile
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from terminal_deployer.build import build_zipapp
from terminal_deployer.models import (
    DeploymentError,
    DeploymentManifest,
    HostDeploymentConfig,
)
from terminal_deployer.runner import CommandResult
from terminal_deployer.service import TerminalDeployer

PREVIOUS_IMAGE = "sha256:" + "1" * 64
CANDIDATE_IMAGE = "sha256:" + "2" * 64


class FakeRunner:
    def __init__(
        self,
        config: HostDeploymentConfig,
        *,
        fail_candidate_health: bool = False,
        fail_candidate_start: bool = False,
        fail_live_migration: bool = False,
        fail_stop: bool = False,
    ) -> None:
        self.config = config
        self.fail_candidate_health = fail_candidate_health
        self.fail_candidate_start = fail_candidate_start
        self.fail_live_migration = fail_live_migration
        self.fail_stop = fail_stop
        self.commands: list[tuple[list[str], dict[str, str] | None]] = []
        self.images = {
            config.live_image: PREVIOUS_IMAGE,
            "al1s-terminal-next:candidate": CANDIDATE_IMAGE,
        }
        self.running_image: str | None = PREVIOUS_IMAGE

    def run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 120.0,
        check: bool = True,
    ) -> CommandResult:
        del cwd, timeout
        self.commands.append((command, env))
        result = self._result(command, env)
        if check and result.returncode:
            raise DeploymentError(result.stderr or "fake command failed")
        return result

    def _result(self, command: list[str], env: dict[str, str] | None) -> CommandResult:
        if (
            command[:4] == ["docker", "image", "inspect", "--format"]
            and command[4] == "{{.Architecture}}"
        ):
            return CommandResult(0, "arm64\n")
        if command[:3] == ["docker", "image", "inspect"]:
            image = command[-1]
            image_id = self.images.get(image, image if image.startswith("sha256:") else None)
            return CommandResult(0, f"{image_id}\n") if image_id else CommandResult(1)
        if command[:3] == ["docker", "load", "--input"]:
            return CommandResult(0, "Loaded image\n")
        if command[:2] == ["docker", "tag"]:
            source, target = command[2:4]
            self.images[target] = self.images.get(source, source)
            return CommandResult(0)
        if command[:3] == ["docker", "compose", "--env-file"]:
            if "stop" in command or "rm" in command:
                if self.fail_stop:
                    return CommandResult(1, stderr="stop failed")
                self.running_image = None
            elif "up" in command:
                assert env is not None
                self.running_image = self.images[env["AL1S_TERMINAL_IMAGE"]]
                if self.running_image == CANDIDATE_IMAGE:
                    (self.config.data_dir / "terminal.db").write_bytes(b"new-execution-result")
                    if self.fail_candidate_start:
                        return CommandResult(1, stderr="up response lost")
            return CommandResult(0)
        if command[:2] == ["docker", "inspect"]:
            if self.running_image is None:
                return CommandResult(1, stderr="missing")
            health = (
                "unhealthy"
                if self.fail_candidate_health and self.running_image == CANDIDATE_IMAGE
                else "healthy"
            )
            return CommandResult(0, f"running|{health}|{self.running_image}\n")
        if command[:2] == ["docker", "run"]:
            self._simulate_live_migration(command)
            if self.fail_live_migration and f"{self.config.data_dir.resolve()}:/data" in command:
                return CommandResult(1, stderr="migration failed")
            return CommandResult(0)
        raise AssertionError(f"unexpected command: {command}")

    def _simulate_live_migration(self, command: list[str]) -> None:
        mounts = [command[index + 1] for index, value in enumerate(command[:-1]) if value == "-v"]
        if f"{self.config.data_dir.resolve()}:/data" in mounts:
            (self.config.data_dir / "terminal.db").write_bytes(b"migrated-schema")


def _config(tmp_path: Path) -> HostDeploymentConfig:
    root = tmp_path / "terminal"
    deploy = root / "deploy"
    deploy.mkdir(parents=True)
    compose = deploy / "compose.arm64.yaml"
    environment = deploy / "terminal.arm64.env"
    compose.write_text("services: {}\n", encoding="utf-8")
    environment.write_text("AL1S_TERMINAL_PLATFORM_URL=https://al1s.local\n", encoding="utf-8")
    data = root / "data"
    data.mkdir()
    adb = root / "adb-home"
    adb.mkdir()
    models = tmp_path / "models"
    models.mkdir()
    certs = root / "certs"
    certs.mkdir()
    return HostDeploymentConfig(
        root=root,
        compose_file=compose,
        env_file=environment,
        data_dir=data,
        adb_home=adb,
        model_dir=models,
        cert_dir=certs,
        health_timeout_seconds=1,
    )


def _manifest(config: HostDeploymentConfig) -> DeploymentManifest:
    archive = config.artifact_dir / "candidate.tar"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"candidate-image")
    return DeploymentManifest(
        deployment_id="release-001",
        archive_name=archive.name,
        archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        candidate_image="al1s-terminal-next:candidate",
        expected_image_id=CANDIDATE_IMAGE,
    )


def _seed_critical_state(config: HostDeploymentConfig) -> bytes:
    database = config.data_dir / "terminal.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE original_state (value TEXT NOT NULL)")
        connection.execute("INSERT INTO original_state VALUES ('preserve-me')")
        connection.commit()
    original = database.read_bytes()
    secret = config.data_dir / "secrets" / "terminal-credential"
    secret.parent.mkdir()
    secret.write_text("credential", encoding="utf-8")
    return original


def test_successful_deployment_preflights_before_cutover_and_is_idempotent(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _seed_critical_state(config)
    manifest = _manifest(config)
    runner = FakeRunner(config)
    deployer = TerminalDeployer(config, runner=runner, sleep=lambda _seconds: None)

    first = deployer.deploy(manifest)
    command_count = len(runner.commands)
    second = deployer.deploy(manifest)

    assert first.status == second.status == "succeeded"
    assert first.image_id == CANDIDATE_IMAGE
    assert first.previous_image_id == PREVIOUS_IMAGE
    assert runner.running_image == CANDIDATE_IMAGE
    assert len(runner.commands) == command_count
    assert any(
        command[:2] == ["docker", "run"] and "al1s_terminal.providers.rknn_smoke" in command
        for command, _env in runner.commands
    )


@pytest.mark.parametrize("failed_start", [False, True])
def test_started_candidate_failure_preserves_new_data(tmp_path: Path, failed_start: bool) -> None:
    config = _config(tmp_path)
    _seed_critical_state(config)
    manifest = _manifest(config)
    runner = FakeRunner(config, fail_candidate_health=True, fail_candidate_start=failed_start)

    with pytest.raises(DeploymentError, match="current database was preserved"):
        TerminalDeployer(config, runner=runner, sleep=lambda _seconds: None).deploy(manifest)

    assert runner.running_image is None
    assert (config.data_dir / "terminal.db").read_bytes() == b"new-execution-result"
    state = json.loads((config.state_dir / "release-001.json").read_text())
    assert state["status"] == "recovery_required"
    assert state["database_preserved"] is True
    assert (config.data_dir / "secrets" / "terminal-credential").read_text() == "credential"


def test_failed_migration_before_start_can_restore_backup(tmp_path: Path) -> None:
    config = _config(tmp_path)
    original_database = _seed_critical_state(config)
    manifest = _manifest(config)
    runner = FakeRunner(config, fail_live_migration=True)

    outcome = TerminalDeployer(config, runner=runner, sleep=lambda _seconds: None).deploy(manifest)

    assert outcome.status == "rolled_back"
    compose_commands = [
        command for command, _env in runner.commands
        if command[:2] == ["docker", "compose"]
    ]
    assert any(command[-4:] == ["rm", "--stop", "--force", "terminal-agent"]
               for command in compose_commands)
    assert all("down" not in command and "--remove-orphans" not in command
               for command in compose_commands)
    assert runner.running_image == PREVIOUS_IMAGE
    assert (config.data_dir / "terminal.db").read_bytes() == original_database
    assert (config.data_dir / "secrets" / "terminal-credential").read_text() == "credential"


def test_failed_stop_never_snapshots_or_migrates_live_database(tmp_path: Path) -> None:
    config = _config(tmp_path)
    original_database = _seed_critical_state(config)
    manifest = _manifest(config)
    runner = FakeRunner(config, fail_stop=True)
    with pytest.raises(DeploymentError):
        TerminalDeployer(config, runner=runner, sleep=lambda _seconds: None).deploy(manifest)
    assert (config.data_dir / "terminal.db").read_bytes() == original_database
    assert not (config.deployment_dir / manifest.deployment_id / "rollback-state").exists()


def test_overall_timeout_during_migration_preserves_database_and_never_replays(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    _seed_critical_state(config)
    manifest = _manifest(config)
    clock = [0.0]

    class ExpiringRunner(FakeRunner):
        def _simulate_live_migration(self, command: list[str]) -> None:
            super()._simulate_live_migration(command)
            if f"{self.config.data_dir.resolve()}:/data" in command:
                clock[0] = 1800

    runner = ExpiringRunner(config)
    deployer = TerminalDeployer(config, runner=runner, clock=lambda: clock[0])
    with pytest.raises(DeploymentError, match="deployment_execution_timeout"):
        deployer.deploy(manifest)
    assert (config.data_dir / "terminal.db").read_bytes() == b"migrated-schema"
    state = json.loads((config.state_dir / "release-001.json").read_text())
    assert state["status"] == "recovery_required"
    assert state["result_unknown"] is True
    assert state["execution_timeout_seconds"] == 1800
    assert not any("rm" in command for command, _ in runner.commands)
    before = len(runner.commands)
    with pytest.raises(DeploymentError, match="unfinished or failed state"):
        deployer.deploy(manifest)
    assert len(runner.commands) == before
    with pytest.raises(DeploymentError, match="previous_upgrade_unresolved"):
        TerminalDeployer(config, runner=runner).deploy(
            replace(manifest, deployment_id="different-command"),
        )
    assert len(runner.commands) == before


def test_migration_command_timeout_never_restores_database(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed_critical_state(config)
    manifest = _manifest(config)

    class TimeoutRunner(FakeRunner):
        def _simulate_live_migration(self, command: list[str]) -> None:
            super()._simulate_live_migration(command)
            if f"{self.config.data_dir.resolve()}:/data" in command:
                raise subprocess.TimeoutExpired(command, 300)

    runner = TimeoutRunner(config)
    with pytest.raises(DeploymentError, match="deployment_command_timeout"):
        TerminalDeployer(config, runner=runner).deploy(manifest)
    assert (config.data_dir / "terminal.db").read_bytes() == b"migrated-schema"
    assert not any("rm" in command for command, _ in runner.commands)
    state = json.loads((config.state_dir / "release-001.json").read_text())
    assert state["result_unknown"] is True


def test_archive_hash_mismatch_stops_before_docker(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = _manifest(config)
    runner = FakeRunner(config)
    invalid = DeploymentManifest(
        manifest.deployment_id,
        manifest.archive_name,
        "0" * 64,
        manifest.candidate_image,
        manifest.expected_image_id,
    )

    with pytest.raises(DeploymentError, match="SHA-256 mismatch"):
        TerminalDeployer(config, runner=runner).deploy(invalid)

    assert runner.commands == []


def test_explicit_acceptance_cleans_only_preflight_copy(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _seed_critical_state(config)
    manifest = _manifest(config)
    deployer = TerminalDeployer(config, runner=FakeRunner(config), sleep=lambda _: None)
    deployer.deploy(manifest)
    directory = config.deployment_dir / manifest.deployment_id
    assert (directory / "database-preflight" / "terminal.db").exists()
    live_database = (config.data_dir / "terminal.db").read_bytes()
    deployer.cleanup_after_acceptance(manifest.deployment_id)
    deployer.cleanup_after_acceptance(manifest.deployment_id)
    assert not (directory / "database-preflight").exists()
    assert (directory / "rollback-state" / "terminal.db").exists()
    assert (config.data_dir / "terminal.db").read_bytes() == live_database
    state = json.loads((config.state_dir / "release-001.json").read_text())
    assert state["acceptance_confirmed"] is True
    assert state["preflight_cleanup"] == "completed"


def test_cleanup_refuses_failed_or_unknown_deployment(tmp_path: Path) -> None:
    config = _config(tmp_path)
    deployer = TerminalDeployer(config, runner=FakeRunner(config))
    with pytest.raises(DeploymentError, match="explicitly accepted"):
        deployer.cleanup_after_acceptance("unknown")
    config.state_dir.mkdir()
    (config.state_dir / "failed.json").write_text('{"status":"recovery_required"}')
    with pytest.raises(DeploymentError, match="explicitly accepted"):
        deployer.cleanup_after_acceptance("failed")


def test_online_sqlite_backup_captures_committed_wal_rows(tmp_path: Path) -> None:
    config = _config(tmp_path)
    source = config.data_dir / "terminal.db"
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE facts (value TEXT NOT NULL)")
        connection.execute("INSERT INTO facts VALUES ('committed')")
        connection.commit()
    target = tmp_path / "database-copy"

    TerminalDeployer(config, runner=FakeRunner(config))._copy_database_state(target)

    with closing(sqlite3.connect(target / "terminal.db")) as copied:
        assert copied.execute("SELECT value FROM facts").fetchall() == [("committed",)]


def test_manifest_rejects_external_image_tags(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "deployment_id": "release-001",
                "archive_name": "candidate.tar",
                "archive_sha256": "0" * 64,
                "candidate_image": "untrusted/image:latest",
                "expected_image_id": CANDIDATE_IMAGE,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DeploymentError, match="candidate_image"):
        DeploymentManifest.from_path(path)


def test_unknown_crash_state_is_not_replayed_with_same_deployment_id(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manifest = _manifest(config)
    state_dir = config.state_dir
    state_dir.mkdir(parents=True)
    (state_dir / f"{manifest.deployment_id}.json").write_text(
        json.dumps({"deployment_id": manifest.deployment_id, "status": "cutover"}),
        encoding="utf-8",
    )
    runner = FakeRunner(config)

    with pytest.raises(DeploymentError, match="unfinished or failed state"):
        TerminalDeployer(config, runner=runner).deploy(manifest)

    assert runner.commands == []


def test_deployer_builds_as_a_dependency_free_zipapp(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[1]
    target = build_zipapp(source_root, tmp_path / "al1s-terminal-deployer.pyz")

    completed = subprocess.run(
        [sys.executable, "-I", "-S", str(target), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "manifest" in completed.stdout
    with zipfile.ZipFile(target) as archive:
        assert "terminal_deployer/service.py" in archive.namelist()
        assert "__main__.py" in archive.namelist()
