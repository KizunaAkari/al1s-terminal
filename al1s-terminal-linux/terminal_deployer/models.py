from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

DEPLOYMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,239}\.tar$")
IMAGE_REFERENCE = re.compile(r"^al1s-terminal(?:-next)?:[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
IMAGE_ID = re.compile(r"^sha256:[a-f0-9]{64}$")


class DeploymentError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DeploymentManifest:
    deployment_id: str
    archive_name: str
    archive_sha256: str
    candidate_image: str
    expected_image_id: str

    @classmethod
    def from_path(cls, path: Path) -> DeploymentManifest:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeploymentError("deployment manifest is unreadable") from exc
        if not isinstance(payload, dict):
            raise DeploymentError("deployment manifest must be a JSON object")
        manifest = cls(
            deployment_id=_required_string(payload, "deployment_id"),
            archive_name=_required_string(payload, "archive_name"),
            archive_sha256=_required_string(payload, "archive_sha256").lower(),
            candidate_image=_required_string(payload, "candidate_image"),
            expected_image_id=_required_string(payload, "expected_image_id").lower(),
        )
        manifest.validate()
        return manifest

    def validate(self) -> None:
        checks = (
            (DEPLOYMENT_ID, self.deployment_id, "deployment_id"),
            (ARTIFACT_NAME, self.archive_name, "archive_name"),
            (SHA256, self.archive_sha256, "archive_sha256"),
            (IMAGE_REFERENCE, self.candidate_image, "candidate_image"),
            (IMAGE_ID, self.expected_image_id, "expected_image_id"),
        )
        for pattern, value, name in checks:
            if pattern.fullmatch(value) is None:
                raise DeploymentError(f"invalid {name}")


@dataclass(frozen=True, slots=True)
class HostDeploymentConfig:
    root: Path
    compose_file: Path
    env_file: Path
    data_dir: Path
    adb_home: Path
    model_dir: Path
    cert_dir: Path
    live_image: str = "al1s-terminal-next:stable"
    rollback_image: str = "al1s-terminal-next:rollback"
    service_name: str = "terminal-agent"
    container_name: str = "al1s-terminal-linux"
    health_timeout_seconds: float = 150.0

    @property
    def artifact_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def deployment_dir(self) -> Path:
        return self.root / "deployments"

    @property
    def state_dir(self) -> Path:
        return self.root / "deployment-state"

    @property
    def lock_path(self) -> Path:
        return self.root / "deployment.lock"

    def validate(self) -> None:
        if not IMAGE_REFERENCE.fullmatch(self.live_image):
            raise DeploymentError("invalid live image reference")
        if not IMAGE_REFERENCE.fullmatch(self.rollback_image):
            raise DeploymentError("invalid rollback image reference")
        for path, name, must_exist in (
            (self.compose_file, "compose_file", True),
            (self.env_file, "env_file", True),
            (self.data_dir, "data_dir", False),
            (self.adb_home, "adb_home", False),
            (self.cert_dir, "cert_dir", False),
        ):
            _require_within(path, self.root, name)
            if must_exist and not path.is_file():
                raise DeploymentError(f"{name} does not exist")
        if not self.model_dir.is_dir():
            raise DeploymentError("model_dir does not exist")
        if self.health_timeout_seconds <= 0:
            raise DeploymentError("health timeout must be positive")


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DeploymentError(f"manifest field {key} is required")
    return value.strip()


def _require_within(path: Path, root: Path, name: str) -> None:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise DeploymentError(f"{name} must stay inside deployment root")
