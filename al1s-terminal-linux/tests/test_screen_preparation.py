import subprocess

import pytest

from al1s_terminal.interactive.sessions import InteractiveSessionManager
from al1s_terminal.providers.screen_preparation import prepare_screen


class Phone:
    def __init__(self, *, awake=False, locked=True, secure=False, unknown=False):
        self.awake, self.locked, self.secure, self.unknown = awake, locked, secure, unknown
        self.commands = []

    def run(self, args, timeout):
        self.commands.append(tuple(args[3:]))
        assert 0 < timeout <= 8
        if tuple(args[3:]) == ("shell", "dumpsys", "power"):
            output = "mWakefulness=" + ("Awake" if self.awake else "Dozing")
        elif tuple(args[3:]) == ("shell", "dumpsys", "window", "policy"):
            output = (
                ""
                if self.unknown
                else f"showing={str(self.locked).lower()}\nsecure={str(self.secure).lower()}"
            )
        elif tuple(args[3:]) == ("shell", "input", "keyevent", "KEYCODE_WAKEUP"):
            self.awake = True
            output = ""
        elif tuple(args[3:]) == ("shell", "wm", "dismiss-keyguard"):
            assert not self.secure
            self.locked = False
            output = ""
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, output, "")


def test_sleeping_phone_is_woken_and_nonsecure_keyguard_removed_without_home():
    phone = Phone()
    prepare_screen("adb", "phone", phone.run)
    assert phone.awake and not phone.locked
    assert ("shell", "input", "keyevent", "KEYCODE_WAKEUP") in phone.commands
    assert all("KEYCODE_HOME" not in cmd and "26" not in cmd for cmd in phone.commands)
    inputs = len([cmd for cmd in phone.commands if "input" in cmd or "wm" in cmd])
    prepare_screen("adb", "phone", phone.run)
    assert len([cmd for cmd in phone.commands if "input" in cmd or "wm" in cmd]) == inputs


@pytest.mark.parametrize("options", [{"secure": True}, {"unknown": True}])
def test_credential_or_unknown_lock_state_never_dispatches_input(options):
    phone = Phone(**options)
    with pytest.raises(RuntimeError):
        prepare_screen("adb", "phone", phone.run)
    assert not any("input" in cmd or "wm" in cmd for cmd in phone.commands)


def test_view_only_or_expired_authority_never_wakes_and_current_control_can_prepare():
    manager = InteractiveSessionManager()
    token = manager.create("phone").token
    called = []
    assert not manager.prepare_screen(token, lambda serial: called.append(serial))
    manager.confirm_platform(30)
    assert manager.prepare_screen(token, lambda serial: called.append(serial))
    manager.begin_automation("phone")
    assert not manager.prepare_screen(token, lambda serial: called.append(serial))
    assert called == ["phone"]


def test_input_authority_is_rechecked_before_physical_wake():
    phone = Phone()
    with pytest.raises(RuntimeError, match="interactive_control_disabled"):
        prepare_screen("adb", "phone", phone.run, allowed=lambda: False)
    assert not any("input" in cmd or "wm" in cmd for cmd in phone.commands)


def test_adb_timeout_has_a_bounded_preparation_error():
    def timed_out(args, timeout):
        raise subprocess.TimeoutExpired(args, timeout)

    with pytest.raises(RuntimeError, match="screen_prepare_timeout"):
        prepare_screen("adb", "phone", timed_out)
