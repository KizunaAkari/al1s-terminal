import asyncio
import struct
from types import SimpleNamespace

import pytest
from websockets.exceptions import ConnectionClosedError

from al1s_terminal.interactive.relay import AdbScrcpyRelay
from al1s_terminal.interactive.sessions import InteractiveChannel, InteractiveSessionError


def test_revoked_control_close_is_not_handler_failure():
    async def run():
        relay = AdbScrcpyRelay(host="127.0.0.1", port=0, public_url="ws://localhost")
        relay.confirm_platform(30)
        session = relay._manager.create("phone")
        relay._streams[session.token] = SimpleNamespace(control=Writer())

        class ClosedSocket(Socket):
            async def __aiter__(self):
                raise ConnectionClosedError(None, None)
                yield  # pragma: no cover

        await relay._handler(ClosedSocket(), f"/scrcpy/{session.token}/control")
        assert relay._manager.snapshots()[0].control_connections == 0

    asyncio.run(run())


class Socket:
    def __init__(self, messages=()):
        self.messages = messages
        self.sent = []
        self.closed = []

    async def __aiter__(self):
        for message in self.messages:
            yield message
        await asyncio.Event().wait()

    async def send(self, message):
        self.sent.append(message)

    async def close(self, **kwargs):
        self.closed.append(kwargs)


class Writer:
    def __init__(self):
        self.sent = []

    def write(self, message):
        self.sent.append(message)

    async def drain(self):
        pass


def test_video_upstream_never_enters_device_writer():
    async def run():
        writer = Writer()
        closed = []

        async def close():
            closed.append(True)

        stream = SimpleNamespace(video=asyncio.StreamReader(), control=writer, close=close)

        async def open_stream(serial):
            assert serial == "phone"
            return stream

        relay = AdbScrcpyRelay(
            host="127.0.0.1",
            port=0,
            public_url="ws://localhost",
            process_factory=SimpleNamespace(open=open_stream),
        )
        session = relay._manager.create("phone")
        socket = Socket([b"000chost:version", b"shell:input tap 1 1"])
        await asyncio.wait_for(relay._handler(socket, f"/scrcpy/{session.token}/video"), 1)
        assert not writer.sent
        assert socket.closed[0]["code"] == 1008
        assert closed == [True] and not relay._streams

    asyncio.run(run())


def test_control_rechecks_gate_before_each_packet():
    async def run():
        relay = AdbScrcpyRelay(host="127.0.0.1", port=0, public_url="ws://localhost")
        relay.confirm_platform(30)
        session = relay._manager.create("phone")
        writer = Writer()
        relay._streams[session.token] = SimpleNamespace(control=writer)
        packet = struct.pack(">BBIII", 0, 0, 3, 0, 0)

        class DisconnectingSocket(Socket):
            async def __aiter__(self):
                yield packet
                relay.disconnect_platform()
                yield packet

        socket = DisconnectingSocket()
        relay._manager.attach(session.token, InteractiveChannel.CONTROL, socket)
        with pytest.raises(InteractiveSessionError):
            await relay._control(session.token, socket)
        assert writer.sent == [packet]

    asyncio.run(run())


def test_video_bytes_remain_unchanged():
    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(b"encoded-video-not-decoded")
        reader.feed_eof()

        async def close():
            pass

        async def open_stream(serial):
            return SimpleNamespace(video=reader, close=close)

        relay = AdbScrcpyRelay(
            host="127.0.0.1",
            port=0,
            public_url="ws://localhost",
            process_factory=SimpleNamespace(open=open_stream),
        )
        session = relay._manager.create("phone")
        socket = Socket()
        await asyncio.wait_for(relay._handler(socket, f"/scrcpy/{session.token}/video"), 1)
        assert b"".join(socket.sent) == b"encoded-video-not-decoded"
        assert relay._manager.get(session.token).video_connections == 0
        # Reuse the authorized lease after EOF; no platform round trip or new token.
        await relay._handler(Socket(), f"/scrcpy/{session.token}/video")
        assert relay._manager.get(session.token).video_connections == 0

    asyncio.run(run())


def test_peer_disconnect_cancels_unfinished_native_startup():
    async def run():
        started, cancelled, disconnected = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def open_stream(serial):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        class ClosingSocket(Socket):
            async def wait_closed(self):
                await disconnected.wait()

        relay = AdbScrcpyRelay(
            host="127.0.0.1",
            port=0,
            public_url="ws://localhost",
            process_factory=SimpleNamespace(open=open_stream),
        )
        session = relay._manager.create("phone")
        handler = asyncio.create_task(
            relay._handler(ClosingSocket(), f"/scrcpy/{session.token}/video")
        )
        await started.wait()
        disconnected.set()
        await asyncio.wait_for(handler, 1)
        assert cancelled.is_set()
        assert relay._manager.get(session.token).video_connections == 0

    asyncio.run(run())
