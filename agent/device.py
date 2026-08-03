import base64
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


class AndroidDevice:
    """Small ADB adapter. Root-only operations are opt-in via environment commands."""

    def __init__(self, serial: str = "", workdir: str = "./agent-data"):
        self.serial = serial
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._launcher_package_cache = ""
        self._static_state_cache: dict[str, Any] = {}

    def _adb(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        command = ["adb"]
        if self.serial:
            command += ["-s", self.serial]
        command += list(args)
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)

    def _shell(self, *args: str, timeout: int = 30) -> str:
        result = self._adb("shell", *args, timeout=timeout)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "adb shell failed")
        return result.stdout.strip()

    def is_connected(self) -> bool:
        result = self._adb("get-state", timeout=10)
        return result.returncode == 0 and result.stdout.strip() == "device"

    def state(self) -> dict[str, Any]:
        try:
            connected = self.is_connected()
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            return {"connected": False, "serial": self.serial or "default", "warning": str(exc)}
        data: dict[str, Any] = {"connected": connected, "serial": self.serial or "default", "root": False}
        if connected:
            try:
                battery = self._shell("dumpsys", "battery", timeout=10)
                data["battery_level"] = self._property_int(battery, "level")
                data["battery_status"] = self._property(battery, "status")
                power = self._shell("dumpsys", "power", timeout=10)
                data["screen_on"] = "Wakefulness=Awake" in power or "state=ON" in power
                data["storage"] = self.phone_storage()
            except Exception as exc:
                data["warning"] = str(exc)
            if not self._static_state_cache:
                try:
                    root_check = self._adb("shell", "su", "-c", "id", timeout=10)
                    self._static_state_cache = {
                        "model": self._shell("getprop", "ro.product.model", timeout=10),
                        "android_version": self._shell("getprop", "ro.build.version.release", timeout=10),
                        "root": root_check.returncode == 0 and "uid=0(root)" in root_check.stdout,
                    }
                except Exception as exc:
                    data["warning"] = str(exc)
            data.update(self._static_state_cache)
        return data

    def phone_storage(self) -> dict[str, int]:
        output = self._shell("df", "-k", "/data", timeout=10)
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if len(lines) < 2:
            raise RuntimeError("无法读取 Android /data 存储空间")
        parts = lines[-1].split()
        if len(parts) < 6:
            raise RuntimeError(f"无法解析 Android 存储空间：{lines[-1]}")
        try:
            total_bytes = int(parts[-5]) * 1024
            used_bytes = int(parts[-4]) * 1024
            free_bytes = int(parts[-3]) * 1024
        except ValueError as exc:
            raise RuntimeError(f"无法解析 Android 存储空间：{lines[-1]}") from exc
        return {
            "path": "/data",
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "free_bytes": free_bytes,
        }

    @staticmethod
    def _property(text: str, name: str) -> str | None:
        prefix = f"{name}:"
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith(prefix):
                return stripped.split(":", 1)[1].strip()
        return None

    @classmethod
    def _property_int(cls, text: str, name: str) -> int | None:
        value = cls._property(text, name)
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None

    def reboot(self) -> dict[str, Any]:
        result = self._adb("reboot", timeout=10)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "adb reboot failed")
        return {"accepted": True}

    def wake(self) -> dict[str, Any]:
        self._shell("input", "keyevent", "KEYCODE_WAKEUP")
        return {"accepted": True}

    def sleep(self) -> dict[str, Any]:
        self._shell("input", "keyevent", "KEYCODE_SLEEP")
        return {"accepted": True}

    def start_session(self) -> dict[str, Any]:
        try:
            connected = self.is_connected()
        except (FileNotFoundError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"开始检查失败：Android 手机未连接（{exc}）") from exc
        if not connected:
            raise RuntimeError("开始检查失败：Android 手机未连接（ADB offline）")
        self.wake()
        time.sleep(0.2)
        # This succeeds on devices without a secure lock and is harmless when unsupported.
        self._adb("shell", "wm", "dismiss-keyguard", timeout=10)
        launcher = self._home_package()
        self.home()
        foreground = ""
        deadline = time.monotonic() + 1.5
        while True:
            foreground = self._foreground_package()
            if not launcher or foreground == launcher:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.15)
        if launcher and foreground and foreground != launcher:
            raise RuntimeError(f"开始检查失败：无法返回手机主界面（当前 {foreground}，主页 {launcher}）")
        after = self.state()
        if not after.get("connected"):
            raise RuntimeError("开始检查失败：唤醒后 Android 手机失去连接")
        if after.get("screen_on") is False:
            raise RuntimeError("开始检查失败：无法点亮 Android 屏幕")
        return {"accepted": True, "device": after, "home": True, "launcher": launcher, "foreground": foreground}

    def _home_package(self) -> str:
        if self._launcher_package_cache:
            return self._launcher_package_cache
        try:
            output = self._shell(
                "cmd", "package", "resolve-activity", "--brief",
                "-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME",
                timeout=10,
            )
        except RuntimeError:
            return ""
        component = next((line.strip() for line in reversed(output.splitlines()) if "/" in line), "")
        self._launcher_package_cache = component.split("/", 1)[0]
        return self._launcher_package_cache

    @staticmethod
    def _component_from_dumpsys(output: str) -> str:
        component_pattern = re.compile(r"([A-Za-z][A-Za-z0-9_.$]*/[A-Za-z0-9_.$]+)")
        for marker in ("mResumedActivity", "topResumedActivity", "mCurrentFocus", "mFocusedApp"):
            for line in output.splitlines():
                if marker not in line:
                    continue
                match = component_pattern.search(line)
                if match:
                    return match.group(1)
        return ""

    def _foreground_component(self) -> str:
        for command in (("dumpsys", "activity", "activities"), ("dumpsys", "window")):
            try:
                output = self._shell(*command, timeout=10)
            except RuntimeError:
                continue
            component = self._component_from_dumpsys(output)
            if component:
                return component
        return ""

    def _foreground_package(self) -> str:
        component = self._foreground_component()
        return component.split("/", 1)[0] if component else ""

    def detect_foreground_app(self) -> dict[str, Any]:
        state = self._adb("get-state", timeout=10)
        if state.returncode != 0 or state.stdout.strip() != "device":
            raise RuntimeError(state.stderr.strip() or "Android 手机未连接")

        component = self._foreground_component()
        launcher = self._home_package()
        if not component:
            return {
                "detected": False,
                "reason": "no_foreground_activity",
                "message": "暂未识别到前台应用，请在左侧手机画面中打开目标应用",
                "launcher": launcher,
            }

        package, activity = component.split("/", 1)
        if package == launcher or package == "com.android.systemui":
            return {
                "detected": False,
                "reason": "launcher_or_system_ui",
                "message": "手机当前位于桌面或系统界面，请打开需要自动化的应用",
                "package": package,
                "activity": activity,
                "component": component,
                "launcher": launcher,
            }
        return {
            "detected": True,
            "package": package,
            "activity": activity,
            "component": component,
            "launcher": launcher,
        }

    def home(self) -> dict[str, Any]:
        self._shell("input", "keyevent", "KEYCODE_HOME")
        return {"accepted": True, "keycode": "HOME"}

    def back(self) -> dict[str, Any]:
        self._shell("input", "keyevent", "KEYCODE_BACK")
        return {"accepted": True, "keycode": "BACK"}

    def set_orientation(self, orientation: str) -> dict[str, Any]:
        if orientation not in {"auto", "portrait", "landscape"}:
            raise ValueError(f"不支持的屏幕方向：{orientation}")
        if orientation == "auto":
            self._shell("settings", "put", "system", "accelerometer_rotation", "1")
        else:
            self._shell("settings", "put", "system", "accelerometer_rotation", "0")
            self._shell("settings", "put", "system", "user_rotation", "1" if orientation == "landscape" else "0")
        return {
            "accepted": True,
            "orientation": orientation,
            "display_size": self._shell("wm", "size", timeout=10),
        }

    def screenshot(self, include_data: bool = True) -> dict[str, Any]:
        target = self.workdir / "latest-screen.png"
        temporary = self.workdir / f".latest-screen-{time.time_ns()}.png"
        # Binary mode is required because PNG data cannot be decoded as text.
        binary_command = ["adb"] + (["-s", self.serial] if self.serial else []) + ["exec-out", "screencap", "-p"]
        raw = subprocess.run(binary_command, capture_output=True, timeout=30, check=False)
        if raw.returncode:
            raise RuntimeError(raw.stderr.decode(errors="replace"))
        temporary.write_bytes(raw.stdout)
        temporary.replace(target)
        result = {"path": str(target), "mime": "image/png", "size_bytes": len(raw.stdout)}
        if include_data:
            result["data_base64"] = base64.b64encode(raw.stdout).decode()
        return result

    def start_recording(self, task_id: str, time_limit_seconds: int = 180) -> dict[str, Any]:
        if not self.is_connected():
            raise RuntimeError("Android 手机未连接，无法开始录屏")
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "", task_id)[:64] or str(time.time_ns())
        recording_dir = self.workdir / "recordings"
        recording_dir.mkdir(parents=True, exist_ok=True)
        session: dict[str, Any] = {
            "safe_id": safe_id,
            "recording_dir": recording_dir,
            "segments": [],
            "errors": [],
            "stop_event": threading.Event(),
            "started_event": threading.Event(),
            "lock": threading.Lock(),
            "current_process": None,
            "started_monotonic": time.monotonic(),
            "time_limit_seconds": max(1, min(int(time_limit_seconds), 180)),
        }
        thread = threading.Thread(
            target=self._recording_loop,
            args=(session,),
            daemon=True,
            name=f"screenrecord-{safe_id[:16]}",
        )
        session["thread"] = thread
        thread.start()
        if not session["started_event"].wait(5):
            session["stop_event"].set()
            raise RuntimeError("Android screenrecord 启动超时")
        time.sleep(0.3)
        if session["errors"] and session.get("current_process") is None:
            raise RuntimeError(session["errors"][-1])
        return session

    def _recording_loop(self, session: dict[str, Any]):
        segment_index = 0
        stop_event: threading.Event = session["stop_event"]
        while not stop_event.is_set():
            segment_index += 1
            remote_path = f"/sdcard/Download/maa-task-{session['safe_id']}-{segment_index:03d}.mp4"
            local_path = Path(session["recording_dir"]) / f"{session['safe_id']}-{segment_index:03d}.mp4"
            self._adb("shell", "rm", "-f", remote_path, timeout=10)
            command = ["adb"] + (["-s", self.serial] if self.serial else []) + [
                "shell", "screenrecord",
                "--bit-rate", "4000000",
                "--time-limit", str(session["time_limit_seconds"]),
                remote_path,
            ]
            try:
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                with session["lock"]:
                    session["current_process"] = process
                session["started_event"].set()
                while process.poll() is None and not stop_event.wait(0.25):
                    pass
                if process.poll() is None:
                    try:
                        process.send_signal(signal.SIGINT)
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=5)
                error = (process.stderr.read() if process.stderr else b"").decode(errors="replace").strip()
                pull = self._adb("pull", remote_path, str(local_path), timeout=120)
                self._adb("shell", "rm", "-f", remote_path, timeout=10)
                if pull.returncode or not local_path.is_file() or local_path.stat().st_size < 16:
                    detail = pull.stderr.strip() or error or "Android 录屏分段为空"
                    session["errors"].append(detail)
                    if not stop_event.is_set():
                        break
                else:
                    session["segments"].append(str(local_path))
            except Exception as exc:
                session["errors"].append(str(exc))
                session["started_event"].set()
                break
            finally:
                with session["lock"]:
                    session["current_process"] = None

    def stop_recording(self, session: dict[str, Any]) -> dict[str, Any]:
        session["stop_event"].set()
        thread: threading.Thread = session["thread"]
        thread.join(timeout=35)
        if thread.is_alive():
            with session["lock"]:
                process = session.get("current_process")
            if process and process.poll() is None:
                process.kill()
            thread.join(timeout=5)
        paths = [Path(path) for path in session["segments"] if Path(path).is_file()]
        if not paths:
            error = "; ".join(session["errors"]) or "Android 录屏文件为空"
            raise RuntimeError(error)
        return {
            "paths": [str(path) for path in paths],
            "mime": "video/mp4" if len(paths) == 1 else "application/zip",
            "size_bytes": sum(path.stat().st_size for path in paths),
            "segment_count": len(paths),
            "duration_seconds": round(time.monotonic() - float(session["started_monotonic"]), 3),
            "time_limit_seconds": int(session["time_limit_seconds"]),
            "errors": list(session["errors"]),
        }

    def launch_app(self, package: str, activity: str = "") -> dict[str, Any]:
        if activity:
            component = activity if "/" in activity else f"{package}/{activity}"
            output = self._shell("am", "start", "-n", component)
            return {"package": package, "activity": activity, "accepted": True, "output": output}
        output = self._shell("monkey", "-p", package, "1")
        return {"package": package, "accepted": True, "output": output}

    def close_app(self, package: str) -> dict[str, Any]:
        self._shell("am", "force-stop", package)
        return {"package": package, "accepted": True}

    def tap(self, x: int, y: int) -> dict[str, Any]:
        self._shell("input", "tap", str(x), str(y))
        return {"x": x, "y": y}

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> dict[str, Any]:
        self._shell("input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms))
        return {"from": [x1, y1], "to": [x2, y2]}

    def set_charging(self, enabled: bool) -> dict[str, Any]:
        command = os.getenv("ANDROID_CHARGING_COMMAND", "").strip()
        if not command:
            return {"supported": False, "message": "未配置 ANDROID_CHARGING_COMMAND；不同手机/内核实现不同"}
        rendered = command.format(enabled="1" if enabled else "0")
        result = subprocess.run(shlex.split(rendered), capture_output=True, text=True, timeout=30, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "charging command failed")
        return {"supported": True, "enabled": enabled, "output": result.stdout.strip()}
