import json
import logging
import os
import platform
import shutil
import socket
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .config import settings
from .device import AndroidDevice
from .executor import ScriptExecutor
from .interactive import AdbWebSocketRelay
from .local_api import start_local_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [agent] %(message)s")
log = logging.getLogger(__name__)


class Agent:
    def __init__(self):
        self.base = settings.server_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {settings.token}"}
        self.http = requests.Session()
        self.http.headers.update(self.headers)
        os.makedirs(settings.workdir, exist_ok=True)
        self.state_path = os.path.join(settings.workdir, "agent-state.json")
        self.accepting_tasks = self._load_accepting_tasks()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.started_monotonic = time.monotonic()
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop = threading.Event()
        self.device = AndroidDevice(settings.adb_serial, settings.workdir)
        self.executor = ScriptExecutor(self.device)
        self.interactive = AdbWebSocketRelay(
            settings.interactive_host,
            settings.interactive_port,
            settings.interactive_public_url,
            settings.adb_server_host,
            settings.adb_server_port,
            settings.interactive_idle_timeout_seconds,
        )
        if not self.interactive.start():
            log.warning("scrcpy relay did not start; editor will use screenshot fallback")
        self.automation_active = False
        self.log_path = os.path.join(settings.workdir, "agent.log")
        if not any(isinstance(h, logging.FileHandler) for h in log.handlers):
            handler = logging.FileHandler(self.log_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [agent] %(message)s"))
            log.addHandler(handler)

    def _load_accepting_tasks(self) -> bool:
        try:
            with open(self.state_path, "r", encoding="utf-8") as stream:
                state = json.load(stream)
            return bool(state.get("accepting_tasks", True))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return True

    def set_accepting_tasks(self, enabled: bool) -> dict[str, Any]:
        self.accepting_tasks = enabled
        temporary = self.state_path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump({"accepting_tasks": enabled, "updated_at": datetime.now(timezone.utc).isoformat()}, stream)
        os.replace(temporary, self.state_path)
        if not enabled:
            self.interactive.stop_all()
        state = self.service_status()
        log.info("task service changed to %s", state["state"])
        return state

    def service_status(self) -> dict[str, Any]:
        state = "busy" if self.automation_active else ("running" if self.accepting_tasks else "stopped")
        return {
            "state": state,
            "accepting_tasks": self.accepting_tasks,
            "automation_active": self.automation_active,
            "started_at": self.started_at,
            "uptime_seconds": round(max(0.0, time.monotonic() - self.started_monotonic), 1),
            "pid": os.getpid(),
        }

    def request(self, method: str, path: str, session: requests.Session | None = None, **kwargs):
        timeout = kwargs.pop("timeout", 30)
        response = (session or self.http).request(method, self.base + path, timeout=timeout, **kwargs)
        response.raise_for_status()
        return response.json()

    def register(self, session: requests.Session | None = None):
        interactive_status = self.interactive.status()
        maa_status = self.executor.maa.status()
        self.request("POST", "/api/agents/register", session=session, json={
            "id": settings.agent_id, "name": settings.name, "os": settings.os_name,
            "address": settings.advertise_url,
            "capabilities": {"android": True, "adb": True, "maa": maa_status["available"],
                              "yolo": self.executor.yolo.available,
                              "script_dsl": True, "script_dsl_version": 2,
                              "modular_scripts": True, "script_composition": True,
                              "composition_resume": True,
                              "quick_test": True,
                              "numeric_ocr": maa_status["ocr_available"],
                              "conditional_step_skip": True, "feedback_email": True,
                              "post_action_assertion": maa_status["available"],
                              "failure_retry": maa_status["available"],
                              "template_match": maa_status["available"],
                              "remote_control": True,
                              "foreground_app_detection": True,
                              "scrcpy_relay": interactive_status["available"],
                              "task_service_control": True,
                              "task_recording": True,
                              "storage_monitoring": True,
                              "charging_control": bool(os.getenv("ANDROID_CHARGING_COMMAND"))},
            "metadata": {"hostname": socket.gethostname(), "kernel": platform.release(),
                         "machine": platform.machine(), "workdir": os.path.abspath(settings.workdir),
                         "execution_backend": "maa", "maa": maa_status,
                         "yolo": self.executor.yolo.status()},
        })
        log.info("registered as %s", settings.agent_id)

    def heartbeat(self, session: requests.Session | None = None):
        interactive_status = self.interactive.status()
        storage = shutil.disk_usage(settings.workdir)
        self.request("POST", f"/api/agents/{settings.agent_id}/heartbeat", session=session, json={
            "device": self.device.state(),
            "metadata": {
                "interactive": interactive_status,
                "device_lease": "automation" if self.automation_active else (
                    "interactive" if interactive_status["active"] else "idle"
                ),
                "service": self.service_status(),
                "execution": {
                    "backend": "maa",
                    "maa": self.executor.maa.status(),
                    "yolo": self.executor.yolo.status(),
                },
                "storage": {
                    "path": os.path.abspath(settings.workdir),
                    "total_bytes": storage.total,
                    "used_bytes": storage.used,
                    "free_bytes": storage.free,
                },
            },
        })

    def start_heartbeat_monitor(self):
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="agent-heartbeat",
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self):
        heartbeat_http = requests.Session()
        heartbeat_http.headers.update(self.headers)
        while not self._heartbeat_stop.is_set():
            try:
                self.heartbeat(session=heartbeat_http)
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    try:
                        self.register(session=heartbeat_http)
                    except Exception as register_exc:
                        log.warning("heartbeat re-registration failed: %s", register_exc)
                else:
                    log.warning("heartbeat failed: %s", exc)
            except Exception as exc:
                log.warning("heartbeat failed: %s", exc)
            self._heartbeat_stop.wait(max(1.0, settings.heartbeat_interval_seconds))

    def result(self, command_id: str, success: bool, value: dict[str, Any]):
        self.request("POST", f"/api/agent/commands/{command_id}/result", json={"success": success, "result": value})

    def execute(self, command: dict[str, Any]):
        kind, payload = command["kind"], command.get("payload", {})
        if payload.get("task_id"):
            log.info("running task %s", payload["task_id"])
        if kind == "task":
            return self.run_task(payload)
        if kind == "agent_start":
            return self.set_accepting_tasks(True)
        if kind == "agent_stop":
            return self.set_accepting_tasks(False)
        if kind == "restart_device": return self.device.reboot()
        if kind == "screenshot": return self.device.screenshot()
        if kind == "sleep": return self.device.sleep()
        if kind == "wake": return self.device.wake()
        if kind == "detect_app": return self.device.detect_foreground_app()
        if kind == "charging": return self.device.set_charging(bool(payload.get("enabled")))
        if kind == "control": return self.execute_control(payload)
        if kind == "interactive_start": return self.start_interactive(payload)
        if kind == "interactive_stop": return {"stopped": self.interactive.stop(str(payload.get("session_token", "")))}
        if kind == "run_script": return self.run_automation(payload.get("content"), payload.get("params", {}))
        if kind == "quick_test": return self.run_quick_test(payload)
        if kind == "run_step":
            return self.run_automation(
                payload.get("content"),
                payload.get("params", {}),
                keep_interactive=bool(payload.get("keep_interactive", False)),
            )
        if kind == "get_logs":
            try:
                with open(self.log_path, "r", encoding="utf-8") as stream:
                    return {"path": self.log_path, "content": stream.read()[-200_000:]}
            except FileNotFoundError:
                return {"path": self.log_path, "content": ""}
        if kind == "open_editor":
            url = payload.get("url", "/editor/" + settings.agent_id)
            if url.startswith("/"): url = self.base + url
            return {"url": url, "opened": False, "message": "headless terminal returns the platform editor URL"}
        raise ValueError(f"unsupported command: {kind}")

    def run_quick_test(self, payload: dict[str, Any]):
        repeat_count = max(1, min(100, int(payload.get("repeat_count") or 1)))
        runs: list[dict[str, Any]] = []
        for run_index in range(1, repeat_count + 1):
            started = time.monotonic()
            try:
                result = self.run_automation(
                    payload.get("content"),
                    payload.get("params", {}),
                    execution_mode="quick_test",
                )
            except Exception as exc:
                failure = getattr(exc, "execution_result", None)
                if not isinstance(failure, dict):
                    failure = {
                        "success": False,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    }
                failure.pop("failure_screenshot", None)
                failure.pop("failure_screenshot_url", None)
                failure.pop("failure_screenshot_error", None)
                failure.update({
                    "quick_test": True,
                    "run_index": run_index,
                    "run_total": repeat_count,
                    "completed_runs": run_index - 1,
                    "duration_seconds": round(time.monotonic() - started, 3),
                })
                setattr(exc, "execution_result", failure)
                raise

            runs.append({
                "run_index": run_index,
                "duration_seconds": round(time.monotonic() - started, 3),
                "backend": result.get("backend"),
                "executed_steps": len(result.get("steps", [])),
            })

        return {
            "success": True,
            "quick_test": True,
            "run_count": repeat_count,
            "runs": runs,
            "execution_mode": "quick_test",
        }

    def run_task(self, payload: dict[str, Any]):
        task_id = str(payload.get("task_id") or "")
        recording_session = None
        recording_error = None
        if bool(payload.get("record_video")):
            try:
                recording_session = self.device.start_recording(
                    task_id,
                    settings.recording_time_limit_seconds,
                )
            except Exception as exc:
                recording_error = f"录屏启动失败：{exc}"
                log.warning(recording_error)

        try:
            result = self.run_automation(
                payload.get("script"),
                payload.get("params", {}),
                task_context={
                    "task_kind": str(payload.get("task_kind") or "standard"),
                    "attempt": int(payload.get("attempt") or 1),
                    "max_retries": int(payload.get("max_retries") or 0),
                },
            )
        except Exception as exc:
            failure = getattr(exc, "execution_result", None)
            recording = self.finish_task_recording(task_id, recording_session) if recording_session else None
            if isinstance(failure, dict):
                if recording:
                    failure["recording"] = recording
                if recording_error:
                    failure["recording_error"] = recording_error
            else:
                if recording:
                    setattr(exc, "recording_result", recording)
                if recording_error:
                    setattr(exc, "recording_error", recording_error)
            raise

        recording = self.finish_task_recording(task_id, recording_session) if recording_session else None
        if recording:
            result["recording"] = recording
        if recording_error:
            result["recording_error"] = recording_error
        return result

    def finish_task_recording(self, task_id: str, session: dict[str, Any] | None) -> dict[str, Any] | None:
        if not session:
            return None
        archive_path: Path | None = None
        try:
            recording = self.device.stop_recording(session)
            segment_paths = [Path(value) for value in recording["paths"]]
            if len(segment_paths) == 1:
                upload_path = segment_paths[0]
                upload_mime = "video/mp4"
            else:
                archive_path = segment_paths[0].parent / f"{segment_paths[0].stem.rsplit('-', 1)[0]}-segments.zip"
                with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
                    for index, segment_path in enumerate(segment_paths, start=1):
                        archive.write(segment_path, arcname=f"segment-{index:03d}.mp4")
                upload_path = archive_path
                upload_mime = "application/zip"

            with upload_path.open("rb") as stream:
                uploaded = self.request(
                    "POST",
                    f"/api/agent/{settings.agent_id}/tasks/{task_id}/recording",
                    files={"recording": (upload_path.name, stream, upload_mime)},
                    timeout=600,
                )
            for segment_path in segment_paths:
                segment_path.unlink(missing_ok=True)
            if archive_path:
                archive_path.unlink(missing_ok=True)
            return {
                "uploaded": True,
                "size_bytes": uploaded.get("size_bytes", recording["size_bytes"]),
                "recording_url": uploaded.get("recording_url"),
                "mime": uploaded.get("mime", upload_mime),
                "segment_count": recording["segment_count"],
                "duration_seconds": recording["duration_seconds"],
                "time_limit_seconds": recording["time_limit_seconds"],
                "segment_errors": recording.get("errors", []),
            }
        except Exception as exc:
            if archive_path:
                archive_path.unlink(missing_ok=True)
            log.warning("task recording finalize failed: %s", exc)
            return {"uploaded": False, "error": str(exc)}

    def start_interactive(self, payload: dict[str, Any]):
        if self.automation_active:
            raise RuntimeError("device is reserved by an automation task")
        state = self.device.state()
        if not state.get("connected"):
            raise RuntimeError("Android device is not connected")
        serial = str(state.get("serial") or "default")
        expected = str(payload.get("device_serial") or serial)
        if expected not in {"default", serial}:
            raise RuntimeError(f"device changed: expected {expected}, current {serial}")
        result = self.interactive.create_session(serial)
        result["lease"] = "interactive"
        return result

    def run_automation(
        self,
        content: str | None,
        params: dict[str, Any],
        keep_interactive: bool = False,
        execution_mode: str | None = None,
        task_context: dict[str, Any] | None = None,
    ):
        if not getattr(self, "accepting_tasks", True):
            raise RuntimeError("terminal task service is stopped")
        if not keep_interactive:
            self.interactive.stop_all()
        self.automation_active = True
        try:
            execution_options: dict[str, Any] = {}
            if execution_mode:
                execution_options["execution_mode"] = execution_mode
            if task_context is not None:
                execution_options["task_context"] = task_context
            return self.executor.run(content, params, **execution_options)
        finally:
            self.automation_active = False

    def execute_control(self, payload: dict[str, Any]):
        action = str(payload.get("action", ""))
        if action == "tap":
            control_result = self.device.tap(int(payload["x"]), int(payload["y"]))
        elif action == "swipe":
            control_result = self.device.swipe(
                int(payload["x1"]), int(payload["y1"]), int(payload["x2"]), int(payload["y2"]),
                int(payload.get("duration_ms", 300)),
            )
        elif action == "back":
            control_result = self.device.back()
        elif action == "home":
            control_result = self.device.home()
        elif action == "wake":
            control_result = self.device.wake()
        elif action == "sleep":
            control_result = self.device.sleep()
        elif action == "orientation":
            control_result = self.device.set_orientation(str(payload.get("orientation", "auto")))
        else:
            raise ValueError(f"unsupported remote control action: {action}")

        if not bool(payload.get("capture_after", True)):
            return {"control": control_result}
        # Android launchers and lock screens often render a black transition frame
        # immediately after an input event. Wait briefly so the returned frame is useful.
        time.sleep(0.55 if action in {"tap", "swipe", "home", "wake", "orientation"} else 0.25)
        return {**self.device.screenshot(), "control": control_result}

    def loop(self):
        registered = False
        while True:
            poll_started = time.monotonic()
            commands: list[dict[str, Any]] = []
            failed = False
            try:
                if not registered:
                    self.register()
                    registered = True
                    self.start_heartbeat_monitor()
                commands = self.request(
                    "GET",
                    "/api/agent/poll",
                    params={
                        "agent_id": settings.agent_id,
                        "wait_seconds": settings.command_wait_seconds,
                        "accept_tasks": self.accepting_tasks,
                        "limit": 1,
                    },
                    timeout=settings.command_wait_seconds + 10,
                ).get("commands", [])
                for command in commands:
                    try:
                        value = self.execute(command)
                        self.result(command["id"], True, value)
                        log.info("command %s (%s) succeeded", command["id"], command["kind"])
                        if command["kind"] in {"agent_start", "agent_stop"}:
                            try:
                                self.heartbeat()
                            except Exception as heartbeat_exc:
                                log.warning("service state heartbeat failed: %s", heartbeat_exc)
                    except Exception as exc:
                        log.exception("command failed")
                        failure = getattr(exc, "execution_result", None)
                        if not isinstance(failure, dict):
                            failure = {
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                                "success": False,
                            }
                            try:
                                failure["failure_screenshot"] = self.device.screenshot()
                            except Exception as screenshot_exc:
                                failure["failure_screenshot_error"] = str(screenshot_exc)
                        recording_result = getattr(exc, "recording_result", None)
                        if recording_result:
                            failure["recording"] = recording_result
                        recording_error = getattr(exc, "recording_error", None)
                        if recording_error:
                            failure["recording_error"] = recording_error
                        failure.setdefault("error", str(exc))
                        self.result(command["id"], False, failure)
            except requests.HTTPError as exc:
                failed = True
                if exc.response is not None and exc.response.status_code == 404:
                    registered = False
                log.warning("control center HTTP error: %s", exc)
            except Exception as exc:
                failed = True
                log.warning("control center unavailable: %s", exc)
            elapsed = time.monotonic() - poll_started
            if failed or (not commands and elapsed < 0.25):
                time.sleep(settings.poll_interval)


def main():
    agent = Agent()
    start_local_api(settings.bind_host, settings.port, agent.device)
    agent.loop()


if __name__ == "__main__":
    main()
