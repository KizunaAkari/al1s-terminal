import asyncio
import hashlib
from pathlib import Path

import pytest

from al1s_terminal.interactive import scrcpy_process
from al1s_terminal.interactive.scrcpy_process import ScrcpyProcessFactory


def test_preview_bounds_bandwidth_and_keyframe_recovery(monkeypatch):
    captured = []

    async def fake_exec(*args, **_kwargs):
        captured.extend(args)
        return object()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    factory = ScrcpyProcessFactory("adb", Path(__file__), "0" * 64)
    asyncio.run(factory._start("phone", "/tmp/server.jar", "12345678"))

    assert "max_size=1600" in captured
    assert "video_bit_rate=3000000" in captured
    assert "max_fps=24" in captured
    assert "video_codec_options=i-frame-interval=1,frame-rate=24" in captured


def test_hash_mismatch_prevents_adb_calls(monkeypatch):
    async def forbidden(*args):
        pytest.fail("ADB must not run before asset validation")

    monkeypatch.setattr(scrcpy_process, "_adb", forbidden)
    factory = ScrcpyProcessFactory("adb", Path(__file__), "0" * 64)
    assert not factory.available()
    with pytest.raises(RuntimeError, match="asset"):
        asyncio.run(factory.open("phone"))


def test_startup_failure_releases_only_owned_forward_and_process(monkeypatch):
    calls = []

    class Process:
        returncode = None
        killed = False

        def kill(self):
            self.killed = True
            self.returncode = -9

        async def wait(self):
            return self.returncode

    process = Process()

    async def adb(binary, serial, *args):
        assert serial == "phone"
        calls.append(args)
        return b"12345" if args[:2] == ("forward", "tcp:0") else b""

    async def start(*args):
        return process

    async def connect(*args):
        raise TimeoutError("no device video socket")

    server = Path(__file__)
    factory = ScrcpyProcessFactory("adb", server, hashlib.sha256(server.read_bytes()).hexdigest())
    monkeypatch.setattr(scrcpy_process, "_adb", adb)
    monkeypatch.setattr(factory, "_start", start)
    monkeypatch.setattr(factory, "_connect", connect)
    with pytest.raises(TimeoutError):
        asyncio.run(factory.open("phone"))
    assert process.killed
    assert calls[-1] == ("forward", "--remove", "tcp:12345")
    assert not any("--remove-all" in call for call in calls)
