import subprocess
from unittest.mock import Mock

from al1s_terminal.providers.adb import AdbProbeResult, AdbProvider, parse_adb_devices
from al1s_terminal.providers.adb_recovery import AdbRecovery


def probe(text):
    return AdbProbeResult(True, "adb", parse_adb_devices(text))


def test_only_known_offline_devices_recover_with_bounded_backoff():
    now = [0.0]
    adb = Mock()
    recovery = AdbRecovery(adb, lambda: now[0])
    state = probe("online device\nlocked unauthorized\nlost offline\nunknown offline")
    known = {"online", "locked", "lost"}
    recovery.observe(state, known)
    adb.reconnect_offline.assert_not_called()
    recovery.observe(state, known)
    adb.reconnect_offline.assert_called_once_with("lost")
    for t in [1, 2, 14]:
        now[0] = t
        recovery.observe(state, known)
    assert adb.reconnect_offline.call_count == 1
    now[0] = 15
    recovery.observe(state, known)
    assert adb.reconnect_offline.call_count == 2
    now[0] = 44
    recovery.observe(state, known)
    assert adb.reconnect_offline.call_count == 2
    now[0] = 45
    recovery.observe(state, known)
    assert recovery._offline["lost"].retry_at == 105
    recovery.observe(probe("lost device"), known)
    assert not recovery._offline


def test_reconnect_rechecks_state_and_never_restarts_server():
    commands = []
    state = ["offline"]

    def run(args, timeout):
        commands.append((args, timeout))
        return subprocess.CompletedProcess(args, 0, f"phone {state[0]}", "")

    adb = AdbProvider(adb_path="adb", runner=run)
    assert adb.reconnect_offline("phone")
    assert commands[-1] == (("adb", "-s", "phone", "reconnect"), 5)
    for value in ["device", "unauthorized"]:
        state[0] = value
        assert not adb.reconnect_offline("phone")
    assert not adb.reconnect_offline("missing")
    assert len([c for c, _ in commands if "reconnect" in c]) == 1
