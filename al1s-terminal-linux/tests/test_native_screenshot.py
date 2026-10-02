import asyncio
import base64
from types import SimpleNamespace

import pytest

from al1s_terminal.interactive import screenshot
from al1s_terminal.interactive.relay import AdbScrcpyRelay
from al1s_terminal.interactive.sessions import (
    InteractiveChannel,
    InteractiveSessionError,
    InteractiveSessionManager,
)

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a2ioAAAAASUVORK5CYII="
)


@pytest.mark.parametrize("failure", [None, "format", "large", "exit"])
def test_capture_fixed_command_and_bounded_output(monkeypatch, failure):
    async def run():
        stream = asyncio.StreamReader()
        stream.feed_data(b"not a png" if failure == "format" else PNG)
        stream.feed_eof()
        process = SimpleNamespace(stdout=stream, returncode=None, killed=False)

        async def wait():
            process.returncode = 1 if failure == "exit" else 0
            return process.returncode

        def kill():
            process.killed = True
            process.returncode = -9

        async def spawn(*args, **kwargs):
            assert args == ("adb", "-s", "bound-device", "exec-out", "screencap", "-p")
            return process

        process.wait, process.kill = wait, kill
        monkeypatch.setattr(screenshot.asyncio, "create_subprocess_exec", spawn)
        if failure == "large":
            monkeypatch.setattr(screenshot, "MAX_SCREENSHOT_BYTES", 8)
        if failure:
            with pytest.raises((ValueError, RuntimeError)):
                await screenshot.capture_png("adb", "bound-device")
            if failure == "large":
                assert process.killed
        else:
            assert await screenshot.capture_png("adb", "bound-device") == PNG
    asyncio.run(run())


def test_cancellation_kills_owned_adb_process(monkeypatch):
    async def run():
        process = SimpleNamespace(stdout=asyncio.StreamReader(), returncode=None, killed=False)

        def kill():
            process.killed = True
            process.returncode = -9

        async def wait():
            return process.returncode

        async def spawn(*args, **kwargs):
            return process

        process.kill, process.wait = kill, wait
        monkeypatch.setattr(screenshot.asyncio, "create_subprocess_exec", spawn)
        task = asyncio.create_task(screenshot.capture_png("adb", "phone"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.killed
    asyncio.run(run())


def test_session_rate_limit_and_revocation_without_granting_control():
    closed = []
    now = [10.0]
    manager = InteractiveSessionManager(clock=lambda: now[0],
        close_connections=lambda sockets, reason: closed.extend(sockets))
    session = manager.create("phone")
    connection = object()
    manager.attach(session.token, InteractiveChannel.SCREENSHOT, connection)
    with pytest.raises(InteractiveSessionError):
        manager.attach(session.token, InteractiveChannel.CONTROL, object())
    manager.detach(session.token, InteractiveChannel.SCREENSHOT, connection)
    with pytest.raises(InteractiveSessionError):
        manager.attach(session.token, InteractiveChannel.SCREENSHOT, connection)
    now[0] += 1
    manager.attach(session.token, InteractiveChannel.SCREENSHOT, connection)
    manager.stop(session.token)
    assert closed == [connection]


@pytest.mark.parametrize("revoked", [False, True])
def test_relay_uses_bound_identity_and_rechecks_before_return(revoked):
    async def run():
        relay = AdbScrcpyRelay(host="localhost", port=0, public_url="ws://localhost")
        session = relay._manager.create("bound-phone")

        async def capture(serial):
            assert serial == "bound-phone"
            if revoked:
                relay._manager.stop(session.token)
            return PNG

        class Socket:
            def __init__(self):
                self.sent = []
                self.closed = []

            async def __aiter__(self):
                await asyncio.Event().wait()
                yield

            async def send(self, data):
                self.sent.append(data)

            async def close(self, **kwargs):
                self.closed.append(kwargs)

        relay._factory = SimpleNamespace(screenshot=capture)
        socket = Socket()
        await relay._handler(socket, f"/scrcpy/{session.token}/screenshot")
        assert socket.sent == ([] if revoked else [PNG])
        assert socket.closed[-1]["code"] == (1008 if revoked else 1000)
    asyncio.run(run())
