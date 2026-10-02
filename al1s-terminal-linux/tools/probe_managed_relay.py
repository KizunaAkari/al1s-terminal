"""Real video with local gate transitions; never send device-control packets."""

import asyncio
import json
import socket
import sys
from pathlib import Path

from websockets.legacy.client import connect

from al1s_terminal.interactive.relay import AdbScrcpyRelay
from al1s_terminal.interactive.scrcpy_process import ScrcpyProcessFactory


async def rejected(url: str) -> None:
    async with connect(url) as connection:
        await asyncio.wait_for(connection.wait_closed(), 3)
        assert connection.close_code == 1008


async def verify(relay: AdbScrcpyRelay, serial: str) -> None:
    session = relay.create_session(serial)
    async with connect(session["video_ws_url"]) as video:
        data = await asyncio.wait_for(video.recv(), 20)
        assert isinstance(data, bytes) and data
        await rejected(session["control_ws_url"])
        relay.confirm_platform(60)
        async with connect(session["control_ws_url"]) as control:
            await asyncio.sleep(0.2)
            assert not control.closed
            await asyncio.to_thread(relay.disconnect_platform)
            await asyncio.wait_for(control.wait_closed(), 3)
            assert control.close_code == 1008 and not video.closed
        await rejected(session["control_ws_url"])
        relay.confirm_platform(60)
        async with connect(session["control_ws_url"]) as control:
            await asyncio.sleep(0.2)
            assert not control.closed
            await asyncio.to_thread(relay.begin_automation, serial)
            await asyncio.wait_for(control.wait_closed(), 3)
            assert control.close_code == 1008 and not video.closed
        await rejected(session["control_ws_url"])
        relay.end_automation(serial)
        # These bytes enter only the candidate receive-only websocket, never ADB.
        await video.send(b"000chost:version")
        await asyncio.wait_for(video.wait_closed(), 3)
        assert video.close_code == 1008
    print(json.dumps({"initial_video_bytes": len(data), "default_readonly": True,
                      "offline_closes_control_keeps_video": True,
                      "automation_closes_control_keeps_video": True,
                      "video_upstream_rejected": True, "device_input_sent": False}))


def main() -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    relay = AdbScrcpyRelay(
        host="127.0.0.1", port=port, public_url=f"ws://127.0.0.1:{port}",
        process_factory=ScrcpyProcessFactory(
            "adb", Path(sys.argv[2]),
            "8588238c9a5a00aa542906b6ec7e6d5541d9ffb9b5d0f6e1bc0e365e2303079e",
        ),
    )
    try:
        asyncio.run(verify(relay, sys.argv[1]))
    finally:
        relay.stop()


if __name__ == "__main__":
    main()
