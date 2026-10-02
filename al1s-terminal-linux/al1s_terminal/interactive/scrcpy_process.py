"""Own a native scrcpy server and its dedicated sockets, never an ADB browser tunnel."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import structlog

from al1s_terminal.interactive.screenshot import capture_png

SCRCPY_VERSION = "3.3.4"


@dataclass
class ScrcpyConnection:
    video: asyncio.StreamReader
    video_writer: asyncio.StreamWriter
    control: asyncio.StreamWriter
    process: asyncio.subprocess.Process
    adb: str
    serial: str
    port: int
    diagnostics: asyncio.Task[None] | None = None

    async def close(self) -> None:
        self.video_writer.close()
        self.control.close()
        try:
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(
                        self.video_writer.wait_closed(),
                        self.control.wait_closed(),
                        return_exceptions=True,
                    ),
                    3,
                )
            if self.process.returncode is None:
                with suppress(ProcessLookupError):
                    self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 3)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        self.process.kill()
                    await self.process.wait()
        finally:
            if self.diagnostics is not None:
                self.diagnostics.cancel()
                with suppress(asyncio.CancelledError):
                    await self.diagnostics
            await _remove_forward(self.adb, self.serial, self.port)


async def _remove_forward(adb: str, serial: str, port: int) -> None:
    # Teardown must not mask the original error or stall the next attachment.
    try:
        await asyncio.wait_for(_adb(adb, serial, "forward", "--remove", f"tcp:{port}"), 2)
    except (OSError, RuntimeError, TimeoutError):
        structlog.get_logger().warning("scrcpy_forward_cleanup_failed", port=port)


async def _drain_diagnostics(process: asyncio.subprocess.Process) -> None:
    """Drain bounded chunks; log only classified flags, never native payload text."""
    stream = getattr(process, "stdout", None)
    if stream is None:
        return
    reported: set[str] = set()
    tail = b""
    while chunk := await stream.read(1024):
        data = (tail + chunk).lower()
        for marker, code in (
            (b"exception", "native_exception"),
            (b"encoder", "encoder_diagnostic"),
            (b"permission denied", "permission_denied"),
            (b"[server] error", "native_error"),
        ):
            if marker in data and code not in reported:
                reported.add(code)
                structlog.get_logger().warning("scrcpy_native_diagnostic", code=code)
        tail = data[-32:]


async def _adb(adb: str, serial: str, *args: str) -> bytes:
    process = await asyncio.create_subprocess_exec(
        adb,
        "-s",
        serial,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 15)
    except BaseException:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        raise
    if process.returncode != 0:
        raise RuntimeError("scrcpy ADB setup failed")
    return output


class ScrcpyProcessFactory:
    def __init__(self, adb: str, server: Path, sha256: str) -> None:
        self.adb, self.server, self.sha256 = adb, server, sha256

    async def screenshot(self, serial: str) -> bytes:
        return await capture_png(self.adb, serial)

    def available(self) -> bool:
        try:
            if not self.server.is_file() or self.server.stat().st_size > 4 * 1024 * 1024:
                return False
            with self.server.open("rb") as source:
                body = source.read(4 * 1024 * 1024 + 1)
            return len(body) <= 4 * 1024 * 1024 and hashlib.sha256(body).hexdigest() == self.sha256
        except OSError:
            return False

    async def open(self, serial: str) -> ScrcpyConnection:
        async with asyncio.timeout(10):
            return await self._open(serial)

    async def _open(self, serial: str) -> ScrcpyConnection:
        if not await asyncio.to_thread(self.available):
            raise RuntimeError("Pinned scrcpy server asset is unavailable")
        remote = f"/data/local/tmp/al1s-scrcpy-{self.sha256}.jar"
        # A content-addressed remote file survives reconnects. Validate it before reuse.
        digest = await _adb(self.adb, serial, "shell", f"sha256sum {remote} 2>/dev/null || true")
        if digest.split()[:1] != [self.sha256.encode()]:
            await _adb(self.adb, serial, "push", str(self.server), remote)
        scid = f"{secrets.randbelow(2**31):08x}"
        raw_port = await _adb(self.adb, serial, "forward", "tcp:0", f"localabstract:scrcpy_{scid}")
        port = int(raw_port.strip())
        if not 1 <= port <= 65535:
            raise RuntimeError("Invalid ADB forwarded port")
        process = None
        diagnostics = None
        writers: list[asyncio.StreamWriter] = []
        try:
            process = await self._start(serial, remote, scid)
            diagnostics = asyncio.create_task(_drain_diagnostics(process))
            video, video_writer = await self._connect(port)
            writers.append(video_writer)
            _, control = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 3)
            writers.append(control)
            return ScrcpyConnection(
                video, video_writer, control, process, self.adb, serial, port, diagnostics
            )
        except BaseException as exc:
            structlog.get_logger().warning(
                "scrcpy_connection_failed",
                error_type=type(exc).__name__,
                returncode=process.returncode if process else None,
            )
            for writer in writers:
                writer.close()
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            await _remove_forward(self.adb, serial, port)
            if diagnostics is not None:
                diagnostics.cancel()
                with suppress(asyncio.CancelledError):
                    await diagnostics
            raise

    async def _start(self, serial: str, remote: str, scid: str) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(
            self.adb,
            "-s",
            serial,
            "shell",
            f"CLASSPATH={remote}",
            "app_process",
            "/",
            "com.genymobile.scrcpy.Server",
            SCRCPY_VERSION,
            f"scid={scid}",
            "tunnel_forward=true",
            "audio=false",
            "control=true",
            "video_codec=h264",
            "send_device_meta=false",
            "send_dummy_byte=true",
            "clipboard_autosync=false",
            "max_size=1600",
            "max_fps=24",
            "video_bit_rate=3000000",
            "video_codec_options=i-frame-interval=1,frame-rate=24",
            "power_on=false",
            "cleanup=false",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

    async def _connect(self, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        async with asyncio.timeout(10):
            while True:
                try:
                    reader, writer = await asyncio.open_connection("127.0.0.1", port)
                except OSError:
                    await asyncio.sleep(0.1)
                    continue
                try:
                    if await asyncio.wait_for(reader.readexactly(1), 1) == b"\0":
                        return reader, writer
                except (asyncio.IncompleteReadError, TimeoutError, OSError):
                    pass
                writer.close()
                with suppress(TimeoutError, OSError):
                    await asyncio.wait_for(writer.wait_closed(), 1)
                await asyncio.sleep(0.1)
