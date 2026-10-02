"""Bounded real-device smoke: video only, no input, no runtime/database changes."""

import asyncio
import json
import platform
import struct
import sys
from pathlib import Path

import websockets

from al1s_terminal.interactive.scrcpy_process import ScrcpyProcessFactory


async def main() -> None:
    factory = ScrcpyProcessFactory(
        "adb",
        Path(sys.argv[2]),
        "8588238c9a5a00aa542906b6ec7e6d5541d9ffb9b5d0f6e1bc0e365e2303079e",
    )
    stream = await factory.open(sys.argv[1])
    sizes = []
    captured = bytearray()
    try:
        async with asyncio.timeout(20):
            metadata = await stream.video.readexactly(12)
            captured.extend(metadata)
            codec, width, height = struct.unpack(">III", metadata)
            assert codec == 0x68323634 and 0 < width <= 1280 and 0 < height <= 1280
            for _ in range(2):
                header = await stream.video.readexactly(12)
                flags, length = struct.unpack(">QI", header)
                assert 0 < length <= 4 * 1024 * 1024
                captured.extend(header)
                captured.extend(await stream.video.readexactly(length))
                sizes.append({"bytes": length, "config": bool(flags >> 63)})
        print(json.dumps({"python": platform.python_version(), "websockets": websockets.__version__,
                          "dimensions": [width, height], "packets": sizes}))
    finally:
        await stream.close()
    print("owned sockets, process and forward closed")
    if len(sys.argv) == 4:
        with Path(sys.argv[3]).open("xb") as output:
            output.write(captured)


if __name__ == "__main__":
    asyncio.run(main())
