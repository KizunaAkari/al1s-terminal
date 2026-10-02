"""Reconcile durable deployment facts; never rerun a migration or restore data here."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from terminal_deployer.models import DEPLOYMENT_ID, IMAGE_ID, HostDeploymentConfig
from terminal_deployer.runner import CommandRunner
from terminal_deployer.state import DeploymentStateStore

SCHEMA_CHECK = """
import sqlite3
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory
import al1s_terminal.persistence.migrations as migrations
root = Path(migrations.__file__).resolve().parents[2]
config = Config(str(root / 'alembic.ini'))
config.set_main_option('script_location', str(root / 'migrations'))
expected = set(ScriptDirectory.from_config(config).get_heads())
db = sqlite3.connect('file:/data/terminal.db?mode=ro', uri=True, timeout=5)
try:
    if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise RuntimeError('database_integrity_unconfirmed')
    actual = {row[0] for row in db.execute('SELECT version_num FROM alembic_version')}
    if actual != expected:
        raise RuntimeError('database_revision_unconfirmed')
    print('AL1S_SCHEMA_OK')
finally:
    db.close()
"""


def host_boot_id() -> str | None:
    path = Path("/proc/sys/kernel/random/boot_id")
    return path.read_text().strip() if path.is_file() else None


def reconcile(
    config: HostDeploymentConfig, runner: CommandRunner, identity: str, boot_id: str | None,
) -> dict[str, Any]:
    """Caller must hold the deployment lock, including while persisting the result."""
    if not DEPLOYMENT_ID.fullmatch(identity):
        raise ValueError("invalid deployment identity")
    store = DeploymentStateStore(config.state_dir)
    state = store.read(identity)
    if not state:
        return {"deployment_id": identity, "resolved": False, "reason": "deployment_record_missing"}
    if state.get("status") in {"succeeded", "rolled_back", "failed"}:
        return {"deployment_id": identity, "resolved": True, "status": state["status"]}
    reason = _inspect(config, runner, state, identity, boot_id)
    if reason not in {
        "safe_pre_cutover_failure", "candidate_verified", "previous_version_verified",
    }:
        return {"deployment_id": identity, "resolved": False, "reason": reason}
    previous_status = state.get("status")
    status = "succeeded" if reason == "candidate_verified" else "failed"
    store.write(identity, status=status, recovery_reason=reason,
                interrupted_status=previous_status, recovered_after_boot=boot_id,
                result_unknown=False)
    return {"deployment_id": identity, "resolved": True, "status": status, "reason": reason}


def _inspect(
    config: HostDeploymentConfig, runner: CommandRunner, state: dict[str, Any],
    identity: str, boot_id: str | None,
) -> str:
    if state.get("recovery_protocol") != 1:
        return "legacy_deployment_requires_inspection"
    # A free flock alone cannot prove an orphan docker client/daemon operation stopped.
    if not boot_id or not state.get("host_boot_id") or boot_id == state["host_boot_id"]:
        return "host_restart_required_to_exclude_orphan_operations"
    helpers = runner.run(
        ["docker", "ps", "-aq", "--filter", f"label=io.al1s.deployment={identity}"], timeout=15,
    )
    if helpers.stdout.strip():
        return "deployment_helpers_require_inspection"
    phase = state.get("last_phase")
    if phase in {"accepted", "verifying", "preflight"}:
        return "safe_pre_cutover_failure"
    if phase not in {"cutover", "candidate_starting"}:
        return "database_or_cutover_requires_inspection"
    result = runner.run(
        ["docker", "inspect", "--format",
         "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.Image}}",
         config.container_name], timeout=15, check=False,
    )
    parts = result.stdout.strip().split("|")
    if result.returncode or len(parts) != 3 or parts[:2] != ["running", "healthy"]:
        return "candidate_identity_or_health_unconfirmed"
    expected = parts[2]
    if not IMAGE_ID.fullmatch(expected):
        return "candidate_identity_or_health_unconfirmed"
    if expected == state.get("image_id"):
        outcome = "candidate_verified"
    elif expected == state.get("previous_image_id"):
        outcome = "previous_version_verified"
    else:
        return "candidate_identity_or_health_unconfirmed"
    schema = runner.run([
        "docker", "run", "--rm", "--read-only", "--network", "none",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--pids-limit", "32", "--memory", "128m", "--cpus", "0.5",
        "--label", f"io.al1s.recovery={identity}",
        "-v", f"{config.data_dir.resolve()}:/data:ro",
        "--entrypoint", "python", expected, "-c", SCHEMA_CHECK,
    ], timeout=30, check=False)
    if schema.returncode or schema.stdout.strip() != "AL1S_SCHEMA_OK":
        return "database_integrity_or_revision_unconfirmed"
    return outcome
