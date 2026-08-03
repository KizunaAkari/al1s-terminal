#!/usr/bin/env python3
"""Small, token-protected host service for deploying the NPU terminal image.

The service intentionally runs on the terminal host, not inside the Agent
container.  It accepts only the fixed image tag and a platform-provided
artifact URL, verifies the archive, and rolls back the previous image when
the new container does not become healthy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen


DEPLOYMENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,239}$")
SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


CONFIG = {
    "bind_host": env("DEPLOYER_BIND_HOST", "0.0.0.0"),
    "port": int(env("DEPLOYER_PORT", "8767")),
    "token": env("DEPLOYER_TOKEN", ""),
    "target_dir": Path(env("DEPLOYER_TARGET_DIR", "/run/media/mmcblk1p8/maa-test-terminal")).resolve(),
    "compose_file": env("DEPLOYER_COMPOSE_FILE", "deploy/terminal/docker-compose.npu.yml"),
    "image_tag": env("DEPLOYER_IMAGE_TAG", "al1s-terminal-agent:npu-arm64"),
    "container_name": env("DEPLOYER_CONTAINER_NAME", "terminal-agent"),
}
STATE_DIR = CONFIG["target_dir"] / "data" / "terminal-deployments"
CACHE_DIR = CONFIG["target_dir"] / ".terminal-deploy-cache"
STATE_LOCK = threading.Lock()


def state_path(deployment_id: str) -> Path:
    if not DEPLOYMENT_ID.fullmatch(deployment_id):
        raise ValueError("invalid deployment id")
    return STATE_DIR / f"{deployment_id}.json"


def read_state(deployment_id: str) -> dict[str, Any] | None:
    try:
        return json.loads(state_path(deployment_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_state(deployment_id: str, **values: Any) -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = state_path(deployment_id)
    current = read_state(deployment_id) or {"deployment_id": deployment_id}
    current.update(values)
    current["updated_at"] = now()
    temporary = target.with_suffix(".json.tmp")
    with STATE_LOCK:
        temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
    return current


def run(command: list[str], *, cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    print(f"[deployer] {' '.join(command)}", flush=True)
    return subprocess.run(
        command,
        cwd=str(cwd or CONFIG["target_dir"]),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def run_checked(command: list[str], *, cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    result = run(command, cwd=cwd, timeout=timeout)
    if result.returncode:
        detail = (result.stderr or result.stdout or "command failed").strip()[-2000:]
        raise RuntimeError(f"{' '.join(command)} failed: {detail}")
    return result


def compose_file_path() -> Path:
    relative = Path(CONFIG["compose_file"])
    target = (CONFIG["target_dir"] / relative).resolve()
    if CONFIG["target_dir"] not in target.parents or not target.is_file():
        raise RuntimeError(f"compose file not found under target directory: {target}")
    return target


def image_id(tag: str) -> str | None:
    result = run(["docker", "image", "inspect", "--format", "{{.Id}}", tag], timeout=30)
    if result.returncode or not result.stdout.strip():
        return None
    return result.stdout.strip()


def container_status() -> tuple[str, str]:
    result = run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}",
            CONFIG["container_name"],
        ],
        timeout=30,
    )
    if result.returncode or not result.stdout.strip():
        return "missing", ""
    status, _, health = result.stdout.strip().partition("|")
    return status, health


def wait_until_healthy(timeout: int = 150) -> None:
    deadline = time.monotonic() + timeout
    last_status = ""
    while time.monotonic() < deadline:
        status, health = container_status()
        last_status = f"{status}|{health}"
        if status == "running" and health in {"", "healthy"}:
            return
        if status in {"dead", "exited"} or health == "unhealthy":
            break
        time.sleep(2)
    raise RuntimeError(f"terminal container did not become healthy: {last_status or 'missing'}")


def download_artifact(url: str, token: str, expected_sha256: str, name: str) -> Path:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("artifact URL must be an HTTP(S) URL")
    if not ARTIFACT_NAME.fullmatch(name) or not name.endswith((".tar", ".tar.gz")):
        raise RuntimeError("invalid terminal artifact name")
    if not SHA256.fullmatch(expected_sha256):
        raise RuntimeError("invalid terminal artifact checksum")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / name
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{name}.", suffix=".download", dir=CACHE_DIR)
    os.close(descriptor)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    try:
        request = Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/octet-stream"})
        with urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            while True:
                chunk = response.read(8 * 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                output.write(chunk)
        actual = digest.hexdigest()
        if actual.lower() != expected_sha256.lower():
            raise RuntimeError(f"artifact checksum mismatch: expected {expected_sha256}, got {actual}")
        temporary.replace(target)
        return target
    finally:
        temporary.unlink(missing_ok=True)


def deploy(payload: dict[str, Any]) -> None:
    deployment_id = str(payload.get("deployment_id") or "")
    artifact_name = str(payload.get("artifact_name") or "")
    artifact_url = str(payload.get("artifact_url") or "")
    expected_sha256 = str(payload.get("artifact_sha256") or "")
    platform_token = str(payload.get("platform_token") or "")
    image_tag = str(payload.get("image_tag") or "")
    if not DEPLOYMENT_ID.fullmatch(deployment_id):
        raise RuntimeError("invalid deployment id")
    if image_tag != CONFIG["image_tag"]:
        raise RuntimeError("image tag is not allowed for this terminal")
    if not platform_token:
        raise RuntimeError("platform token is missing")
    write_state(deployment_id, status="downloading", message="正在下载并校验平台缓存镜像", artifact_name=artifact_name)
    artifact = download_artifact(artifact_url, platform_token, expected_sha256, artifact_name)
    old_image = image_id(CONFIG["image_tag"])
    rollback_tag = f"{CONFIG['image_tag']}-rollback-{deployment_id}"
    native_was_active = False
    try:
        if old_image:
            run_checked(["docker", "tag", CONFIG["image_tag"], rollback_tag], timeout=30)
        write_state(deployment_id, status="loading", message="正在导入 NPU 容器镜像", artifact=str(artifact), previous_image=old_image)
        run_checked(["docker", "load", "-i", str(artifact)], timeout=600)
        if image_id(CONFIG["image_tag"]) is None:
            raise RuntimeError(f"loaded archive does not contain expected image tag {CONFIG['image_tag']}")
        native_status = run(["systemctl", "is-active", "maa-terminal-agent"], timeout=15)
        native_was_active = native_status.returncode == 0 and native_status.stdout.strip() == "active"
        if native_was_active:
            run_checked(["systemctl", "stop", "maa-terminal-agent"], timeout=30)
        write_state(deployment_id, status="starting", message="正在重建终端容器")
        compose = compose_file_path()
        run_checked(["docker", "compose", "-f", str(compose), "up", "-d", "--no-build"], cwd=CONFIG["target_dir"], timeout=180)
        wait_until_healthy()
        write_state(deployment_id, status="succeeded", message="终端 NPU 容器已健康运行", image_tag=CONFIG["image_tag"])
    except Exception as exc:
        message = str(exc)
        print(f"[deployer] deployment failed: {message}", flush=True)
        if old_image:
            try:
                compose = compose_file_path()
                run(["docker", "compose", "-f", str(compose), "down"], cwd=CONFIG["target_dir"], timeout=120)
                run_checked(["docker", "tag", rollback_tag, CONFIG["image_tag"]], timeout=30)
                run_checked(["docker", "compose", "-f", str(compose), "up", "-d", "--no-build"], cwd=CONFIG["target_dir"], timeout=180)
                wait_until_healthy()
                write_state(deployment_id, status="rolled_back", message=f"部署失败，已回滚：{message}")
                return
            except Exception as rollback_error:
                message = f"{message}; rollback failed: {rollback_error}"
        if native_was_active:
            run(["systemctl", "start", "maa-terminal-agent"], timeout=30)
        write_state(deployment_id, status="failed", message=message)


class Handler(BaseHTTPRequestHandler):
    server_version = "AL1S-Terminal-Deployer/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[deployer] {self.address_string()} {format % args}", flush=True)

    def send_json(self, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self) -> bool:
        expected = f"Bearer {CONFIG['token']}"
        return bool(CONFIG["token"]) and self.headers.get("Authorization") == expected

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_json(HTTPStatus.OK, {"ok": True, "service": "terminal-deployer"})
            return
        if not self.authorized():
            self.send_json(HTTPStatus.UNAUTHORIZED, {"detail": "invalid deployer token"})
            return
        prefix = "/deploy/status/"
        if parsed.path.startswith(prefix):
            deployment_id = unquote(parsed.path[len(prefix):])
            if not DEPLOYMENT_ID.fullmatch(deployment_id):
                self.send_json(HTTPStatus.BAD_REQUEST, {"detail": "invalid deployment id"})
                return
            value = read_state(deployment_id)
            if not value:
                self.send_json(HTTPStatus.NOT_FOUND, {"detail": "deployment not found"})
                return
            self.send_json(HTTPStatus.OK, value)
            return
        self.send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})

    def do_POST(self) -> None:
        if not self.authorized():
            self.send_json(HTTPStatus.UNAUTHORIZED, {"detail": "invalid deployer token"})
            return
        if self.path != "/deploy":
            self.send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024:
                raise ValueError("invalid request body")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            deployment_id = str(payload.get("deployment_id") or "")
            if not DEPLOYMENT_ID.fullmatch(deployment_id):
                raise ValueError("invalid deployment id")
            if read_state(deployment_id):
                self.send_json(HTTPStatus.OK, read_state(deployment_id) or {})
                return
            write_state(deployment_id, status="accepted", message="部署请求已接收", created_at=now())
            threading.Thread(target=self.run_deployment, args=(payload,), daemon=True).start()
            self.send_json(HTTPStatus.ACCEPTED, {"deployment_id": deployment_id, "status": "accepted"})
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"detail": str(exc)})

    @staticmethod
    def run_deployment(payload: dict[str, Any]) -> None:
        try:
            deploy(payload)
        except Exception as exc:
            deployment_id = str(payload.get("deployment_id") or "")
            if DEPLOYMENT_ID.fullmatch(deployment_id):
                write_state(deployment_id, status="failed", message=str(exc))
            print(f"[deployer] unhandled deployment error: {exc}", flush=True)


def main() -> None:
    if not CONFIG["token"]:
        raise SystemExit("DEPLOYER_TOKEN is required")
    CONFIG["target_dir"].mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((CONFIG["bind_host"], CONFIG["port"]), Handler)
    print(f"[deployer] listening on {CONFIG['bind_host']}:{CONFIG['port']}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
