"""One bounded native screenshot; never accept an ADB command from the browser."""
from __future__ import annotations

import asyncio
import struct
from contextlib import suppress

MAX_SCREENSHOT_BYTES = 16 * 1024 * 1024


async def capture_png(adb: str, serial: str) -> bytes:
    process = await asyncio.create_subprocess_exec(
        adb, "-s", serial, "exec-out", "screencap", "-p",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(10):
            assert process.stdout is not None
            body = bytearray()
            while chunk := await process.stdout.read(64 * 1024):
                if len(body) + len(chunk) > MAX_SCREENSHOT_BYTES:
                    raise ValueError("Screenshot exceeds byte limit")
                body.extend(chunk)
            if await process.wait() != 0:
                raise RuntimeError("Screenshot capture failed")
            if len(body) < 33 or body[:8] != b"\x89PNG\r\n\x1a\n" or body[12:16] != b"IHDR":
                raise ValueError("Screenshot is not PNG")
            width, height = struct.unpack_from(">II", body, 16)
            if not (0 < width <= 8192 and 0 < height <= 8192 and width * height <= 16_777_216):
                raise ValueError("Screenshot dimensions exceed limit")
            return bytes(body)
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            with suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), 3)
