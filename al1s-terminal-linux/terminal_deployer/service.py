from __future__ import annotations

import hashlib
import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from terminal_deployer.models import (
    DeploymentError,
    DeploymentManifest,
    HostDeploymentConfig,
)
from terminal_deployer.recovery import host_boot_id, reconcile
from terminal_deployer.runner import (
    CommandRunner,
    DeadlineCommandRunner,
    DeploymentDeadlineExceeded,
    LabelledDeploymentRunner,
    SubprocessCommandRunner,
)
from terminal_deployer.state import DeploymentStateStore


@dataclass(frozen=True, slots=True)
class DeploymentOutcome:
    deployment_id: str
    status: str
    image_id: str
    previous_image_id: str | None


class TerminalDeployer:
    _CRITICAL_STATE = (
        Path("terminal.db"),
        Path("terminal.db-wal"),
        Path("terminal.db-shm"),
        Path("secrets/terminal-credential"),
    )

    def __init__(
        self,
        config: HostDeploymentConfig,
        *,
        runner: CommandRunner | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        boot_id: Callable[[], str | None] = host_boot_id,
    ) -> None:
        config.validate()
        self._config = config
        self._runner = runner or SubprocessCommandRunner()
        self._sleep = sleep
        self._clock = clock
        self._boot_id = boot_id
        self._execution_deadline: float | None = None
        self._states = DeploymentStateStore(config.state_dir)

    def deploy(
        self,
        manifest: DeploymentManifest,
        *,
        prepare: Callable[[float], None] | None = None,
        timeout_seconds: float = 1800,
    ) -> DeploymentOutcome:
        manifest.validate()
        if manifest.candidate_image in {self._config.live_image, self._config.rollback_image}:
            raise DeploymentError("candidate image must not overwrite live or rollback tags")
        self._config.root.mkdir(parents=True, exist_ok=True)
        with self._deployment_lock():
            existing = self._states.read(manifest.deployment_id)
            if existing and existing.get("status") in {"succeeded", "rolled_back"}:
                return _outcome_from_state(existing)
            if existing is not None:
                raise DeploymentError(
                    "deployment ID has unfinished or failed state; inspect it and use a new ID"
                )
            unresolved = self._states.unresolved_upgrade()
            if unresolved:
                reconcile(self._config, self._runner, unresolved, self._boot_id())
            self._states.claim_upgrade(manifest.deployment_id, boot_id=self._boot_id())
            return self._deploy_with_deadline(manifest, prepare, timeout_seconds)

    def _deploy_with_deadline(
        self,
        manifest: DeploymentManifest,
        prepare: Callable[[float], None] | None,
        timeout_seconds: float,
    ) -> DeploymentOutcome:
        original_runner = self._runner
        self._execution_deadline = self._clock() + max(0, min(1800, timeout_seconds))
        self._runner = DeadlineCommandRunner(
            LabelledDeploymentRunner(original_runner, manifest.deployment_id),
            self._execution_deadline,
            self._clock,
        )
        self._states.write(
            manifest.deployment_id,
            status="accepted",
            execution_started_at=time.time() - (1800 - min(1800, max(0, timeout_seconds))),
            execution_timeout_seconds=1800,
            host_boot_id=self._boot_id(),
            recovery_protocol=1,
        )
        try:
            self._check_execution_deadline()
            if prepare is not None:
                prepare(self._execution_deadline)
            self._check_execution_deadline()
            return self._deploy_locked(manifest)
        except DeploymentDeadlineExceeded:
            self._states.write(
                manifest.deployment_id,
                status="recovery_required",
                error="deployment_execution_timeout",
                database_preserved=True,
                result_unknown=True,
            )
            raise
        except subprocess.TimeoutExpired as exc:
            self._states.write(
                manifest.deployment_id,
                status="recovery_required",
                error="deployment_command_timeout",
                database_preserved=True,
                result_unknown=True,
            )
            raise DeploymentError("deployment_command_timeout; result unknown") from exc
        except Exception:
            state = self._states.read(manifest.deployment_id)
            # A definitive pre-cutover error never touched the live container/database.
            if state and state.get("status") in {"accepted", "verifying", "preflight"}:
                self._states.write(
                    manifest.deployment_id, status="failed", error="deployment_preflight_failed"
                )
            raise
        finally:
            self._runner = original_runner
            self._execution_deadline = None

    def _check_execution_deadline(self) -> None:
        if self._execution_deadline is not None and self._clock() >= self._execution_deadline:
            raise DeploymentDeadlineExceeded("deployment_execution_timeout")

    def cleanup_after_acceptance(self, deployment_id: str) -> None:
        from terminal_deployer.acceptance_cleanup import cleanup_accepted_preflight

        with self._deployment_lock():
            cleanup_accepted_preflight(self._config, deployment_id)

    def recover(self, deployment_id: str) -> dict[str, Any]:
        with self._deployment_lock():
            return reconcile(self._config, self._runner, deployment_id, self._boot_id())

    def _deploy_locked(self, manifest: DeploymentManifest) -> DeploymentOutcome:
        archive = self._archive_path(manifest)
        self._states.write(manifest.deployment_id, status="verifying")
        self._verify_archive(archive, manifest.archive_sha256)
        previous_image_id = self._inspect_image(self._config.live_image, required=False)
        candidate_image_id = self._load_candidate(archive, manifest)
        self._states.write(
            manifest.deployment_id,
            status="preflight",
            image_id=candidate_image_id,
            previous_image_id=previous_image_id,
        )
        self._preflight(manifest, candidate_image_id)
        return self._cut_over(manifest, candidate_image_id, previous_image_id)

    def _load_candidate(self, archive: Path, manifest: DeploymentManifest) -> str:
        self._runner.run(["docker", "load", "--input", str(archive)], timeout=900)
        image_id = self._inspect_image(manifest.candidate_image, required=True)
        if image_id != manifest.expected_image_id:
            raise DeploymentError("loaded image ID does not match deployment manifest")
        architecture = self._runner.run(
            ["docker", "image", "inspect", "--format", "{{.Architecture}}", image_id],
            timeout=30,
        ).stdout.strip()
        if architecture != "arm64":
            raise DeploymentError(f"candidate image architecture is {architecture or 'unknown'}")
        return image_id

    def _preflight(self, manifest: DeploymentManifest, image_id: str) -> None:
        work_dir = self._deployment_work_dir(manifest.deployment_id)
        database_dir = work_dir / "database-preflight"
        self._copy_database_state(database_dir)
        self._run_import_smoke(image_id)
        self._run_migration(image_id, database_dir)
        self._run_rknn_smoke(image_id)
        (work_dir / "preflight.json").write_text(
            json.dumps({"image_id": image_id, "status": "passed"}, sort_keys=True),
            encoding="utf-8",
        )

    def _cut_over(
        self,
        manifest: DeploymentManifest,
        candidate_image_id: str,
        previous_image_id: str | None,
    ) -> DeploymentOutcome:
        snapshot = self._deployment_work_dir(manifest.deployment_id) / "rollback-state"
        self._states.write(manifest.deployment_id, status="cutover")
        snapshot_ready = False
        startup_attempted = False
        try:
            self._compose("stop", self._config.live_image)
            self._check_execution_deadline()
            self._snapshot_critical_state(snapshot)
            snapshot_ready = True
            if previous_image_id:
                self._runner.run(
                    ["docker", "tag", previous_image_id, self._config.rollback_image], timeout=30
                )
            self._runner.run(
                ["docker", "tag", candidate_image_id, self._config.live_image], timeout=30
            )
            self._run_migration(candidate_image_id, self._config.data_dir)
            self._states.write(manifest.deployment_id, live_migration_completed=True)
            self._states.write(manifest.deployment_id, status="candidate_starting")
            startup_attempted = True
            self._compose("up", self._config.live_image)
            self._wait_until_healthy(candidate_image_id)
        except (DeploymentDeadlineExceeded, subprocess.TimeoutExpired):
            # A Docker timeout does not prove its daemon stopped the operation.
            # Never start rollback or restore a database while its outcome is unknown.
            raise
        except Exception as exc:
            if startup_attempted or not snapshot_ready:
                self._states.write(
                    manifest.deployment_id,
                    status="recovery_required",
                    image_id=candidate_image_id,
                    previous_image_id=previous_image_id,
                    error=str(exc)[:2_000],
                    database_preserved=True,
                )
                # A failed 'up' can still have started a writer. Never restore an
                # old snapshot over potentially new execution facts or secrets.
                self._compose("stop", self._config.live_image)
                raise DeploymentError(
                    "deployment requires recovery; current database was preserved"
                ) from exc
            self._rollback(snapshot, previous_image_id, exc)
            state = self._states.write(
                manifest.deployment_id,
                status="rolled_back" if previous_image_id else "failed",
                error=str(exc)[:2_000],
                image_id=candidate_image_id,
                previous_image_id=previous_image_id,
            )
            if previous_image_id is None:
                raise
            return _outcome_from_state(state)
        state = self._states.write(
            manifest.deployment_id,
            status="succeeded",
            image_id=candidate_image_id,
            previous_image_id=previous_image_id,
        )
        return _outcome_from_state(state)

    def _rollback(
        self,
        snapshot: Path,
        previous_image_id: str | None,
        deployment_error: Exception,
    ) -> None:
        self._compose("down", self._config.live_image, check=False)
        self._restore_critical_state(snapshot)
        if previous_image_id is None:
            return
        try:
            self._runner.run(
                ["docker", "tag", previous_image_id, self._config.live_image], timeout=30
            )
            self._compose("up", self._config.live_image)
            self._wait_until_healthy(previous_image_id)
        except Exception as rollback_error:
            raise DeploymentError(
                f"deployment failed: {deployment_error}; rollback failed: {rollback_error}"
            ) from rollback_error

    def _run_import_smoke(self, image_id: str) -> None:
        self._runner.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "host",
                "--entrypoint",
                "python",
                image_id,
                "-m",
                "al1s_terminal",
                "--help",
            ],
            timeout=60,
        )

    def _run_migration(self, image_id: str, database_dir: Path) -> None:
        database_dir.mkdir(parents=True, exist_ok=True)
        self._runner.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "host",
                "-v",
                f"{database_dir.resolve()}:/data",
                "-e",
                "AL1S_TERMINAL_DATABASE_URL=sqlite:////data/terminal.db",
                "--entrypoint",
                "alembic",
                image_id,
                "upgrade",
                "head",
            ],
            timeout=300,
        )

    def _run_rknn_smoke(self, image_id: str) -> None:
        self._runner.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "host",
                "--privileged",
                "-v",
                f"{self._config.model_dir.resolve()}:/models:ro",
                "--entrypoint",
                "python",
                image_id,
                "-m",
                "al1s_terminal.providers.rknn_smoke",
            ],
            timeout=120,
        )

    def _compose(self, action: str, image: str, *, check: bool = True) -> None:
        command = [
            "docker",
            "compose",
            "--env-file",
            str(self._config.env_file),
            "-f",
            str(self._config.compose_file),
        ]
        if action == "up":
            command.extend(["up", "-d", "--no-build", self._config.service_name])
        elif action == "stop":
            command.extend(["stop", "-t", "90", self._config.service_name])
        elif action == "down":
            command.extend(["rm", "--stop", "--force", self._config.service_name])
        else:
            raise DeploymentError(f"unsupported compose action: {action}")
        self._runner.run(
            command,
            cwd=self._config.compose_file.parent,
            env=self._compose_environment(image),
            timeout=180,
            check=check,
        )

    def _wait_until_healthy(self, expected_image_id: str) -> None:
        deadline = time.monotonic() + self._config.health_timeout_seconds
        last_status = "missing"
        status_format = (
            "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.Image}}"
        )
        while time.monotonic() < deadline:
            result = self._runner.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    status_format,
                    self._config.container_name,
                ],
                timeout=30,
                check=False,
            )
            last_status = result.stdout.strip() or result.stderr.strip() or "missing"
            status, health, image_id = _container_status(last_status)
            if status == "running" and health == "healthy" and image_id == expected_image_id:
                return
            if status in {"dead", "exited"} or health == "unhealthy":
                break
            self._sleep(2)
        raise DeploymentError(f"terminal container did not become healthy: {last_status[:500]}")

    def _snapshot_critical_state(self, snapshot: Path) -> None:
        if snapshot.exists():
            raise DeploymentError("rollback snapshot already exists")
        snapshot.mkdir(parents=True)
        present: list[str] = []
        for relative in self._CRITICAL_STATE:
            source = self._config.data_dir / relative
            if source.is_symlink():
                raise DeploymentError(f"critical state cannot be a symlink: {relative}")
            if source.is_file():
                target = snapshot / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                present.append(relative.as_posix())
        (snapshot / "snapshot.json").write_text(
            json.dumps({"present": present}, sort_keys=True), encoding="utf-8"
        )

    def _restore_critical_state(self, snapshot: Path) -> None:
        manifest_path = snapshot / "snapshot.json"
        if not manifest_path.is_file():
            return
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        present = set(payload.get("present", []))
        for relative in self._CRITICAL_STATE:
            target = self._config.data_dir / relative
            target.unlink(missing_ok=True)
            if relative.as_posix() in present:
                source = snapshot / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

    def _copy_database_state(self, target_dir: Path) -> None:
        if target_dir.exists():
            raise DeploymentError("database preflight directory already exists")
        target_dir.mkdir(parents=True)
        source = self._config.data_dir / "terminal.db"
        if not source.exists():
            return
        if source.is_symlink() or not source.is_file():
            raise DeploymentError("terminal database must be a regular file")
        destination = target_dir / "terminal.db"
        try:
            with (
                closing(
                    sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
                ) as current,
                closing(sqlite3.connect(destination)) as backup,
            ):
                current.backup(backup)
        except sqlite3.Error as exc:
            destination.unlink(missing_ok=True)
            raise DeploymentError("terminal database online backup failed") from exc

    def _archive_path(self, manifest: DeploymentManifest) -> Path:
        archive = (self._config.artifact_dir / manifest.archive_name).resolve()
        if self._config.artifact_dir.resolve() not in archive.parents:
            raise DeploymentError("deployment archive escaped the artifact directory")
        if not archive.is_file():
            raise DeploymentError("deployment archive does not exist")
        return archive

    def _deployment_work_dir(self, deployment_id: str) -> Path:
        target = (self._config.deployment_dir / deployment_id).resolve()
        if self._config.deployment_dir.resolve() not in target.parents:
            raise DeploymentError("deployment work directory escaped its root")
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _verify_archive(self, archive: Path, expected: str) -> None:
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            while chunk := stream.read(8 * 1024 * 1024):
                self._check_execution_deadline()
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise DeploymentError("deployment archive SHA-256 mismatch")

    def _inspect_image(self, image: str, *, required: bool) -> str | None:
        result = self._runner.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            timeout=30,
            check=False,
        )
        image_id = result.stdout.strip()
        if result.returncode or not image_id:
            if required:
                raise DeploymentError(f"image is unavailable after load: {image}")
            return None
        return image_id

    def _compose_environment(self, image: str) -> dict[str, str]:
        return {
            "AL1S_TERMINAL_IMAGE": image,
            "AL1S_TERMINAL_ENV_FILE": str(self._config.env_file),
            "AL1S_TERMINAL_DATA_DIR_HOST": str(self._config.data_dir),
            "AL1S_TERMINAL_ADB_HOME_HOST": str(self._config.adb_home),
            "AL1S_TERMINAL_MODEL_DIR_HOST": str(self._config.model_dir),
            "AL1S_TERMINAL_CERT_DIR_HOST": str(self._config.cert_dir),
        }

    def _deployment_lock(self) -> _FileLock:
        return _FileLock(self._config.lock_path)


class _FileLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._descriptor: int | None = None
        self._locking_module: Any | None = None

    def __enter__(self) -> _FileLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._descriptor = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            self._lock()
        except OSError as exc:
            os.close(self._descriptor)
            self._descriptor = None
            raise DeploymentError("another terminal deployment is already running") from exc
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self._descriptor is not None:
            self._unlock()
            os.close(self._descriptor)
            self._descriptor = None

    def _lock(self) -> None:
        if self._descriptor is None:
            raise DeploymentError("deployment lock is not open")
        if os.name == "nt":
            module = importlib.import_module("msvcrt")
            os.lseek(self._descriptor, 0, os.SEEK_SET)
            if os.fstat(self._descriptor).st_size == 0:
                os.write(self._descriptor, b"\0")
                os.lseek(self._descriptor, 0, os.SEEK_SET)
            module.locking(self._descriptor, module.LK_NBLCK, 1)
        else:
            module = importlib.import_module("fcntl")
            module.flock(self._descriptor, module.LOCK_EX | module.LOCK_NB)
        self._locking_module = module

    def _unlock(self) -> None:
        if self._descriptor is None or self._locking_module is None:
            return
        module = self._locking_module
        if os.name == "nt":
            os.lseek(self._descriptor, 0, os.SEEK_SET)
            module.locking(self._descriptor, module.LK_UNLCK, 1)
        else:
            module.flock(self._descriptor, module.LOCK_UN)
        self._locking_module = None


def _container_status(value: str) -> tuple[str, str, str]:
    parts = value.split("|", 2)
    if len(parts) != 3:
        return value, "", ""
    return parts[0], parts[1], parts[2]


def _outcome_from_state(state: dict[str, object]) -> DeploymentOutcome:
    return DeploymentOutcome(
        deployment_id=str(state["deployment_id"]),
        status=str(state["status"]),
        image_id=str(state["image_id"]),
        previous_image_id=(
            str(state["previous_image_id"]) if state.get("previous_image_id") is not None else None
        ),
    )
