"""Bounded screen preparation using the existing, serial-bound ADB runner."""

import re
import subprocess
import time
from collections.abc import Callable, Sequence

TextRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


def prepare_screen(
    adb: str, serial: str, runner: TextRunner, *, allowed: Callable[[], bool] = lambda: True
) -> None:
    deadline = time.monotonic() + 8

    def command(*args: str, input_action: bool = False) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("screen_prepare_timeout")
        if input_action and not allowed():
            raise RuntimeError("interactive_control_disabled")
        try:
            result = runner((adb, "-s", serial, *args), min(remaining, 5))
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("screen_prepare_timeout") from error
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError("screen_prepare_command_failed") from error
        if result.returncode != 0:
            raise RuntimeError("screen_prepare_command_failed")
        return result.stdout

    def state() -> tuple[bool, bool]:
        policy = command("shell", "dumpsys", "window", "policy")
        secure = re.search(r"\bsecure=(true|false)\b", policy)
        showing = re.search(r"\bshowing=(true|false)\b", policy)
        if secure is None or showing is None:
            raise RuntimeError("screen_state_unknown")
        if secure[1] == "true":
            raise RuntimeError("secure_keyguard")
        power = command("shell", "dumpsys", "power")
        wake = re.search(r"\bmWakefulness=(\w+)", power)
        if wake is None:
            raise RuntimeError("screen_state_unknown")
        return wake[1] == "Awake", showing[1] == "true"

    awake, locked = state()
    if not awake:
        command("shell", "input", "keyevent", "KEYCODE_WAKEUP", input_action=True)
    dismissed = False
    while True:
        awake, locked = state()
        if awake and not locked:
            return
        if awake and locked and not dismissed:
            command("shell", "wm", "dismiss-keyguard", input_action=True)
            dismissed = True
        if time.monotonic() >= deadline:
            raise RuntimeError("screen_prepare_timeout")
        time.sleep(0.1)
