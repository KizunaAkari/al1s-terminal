from __future__ import annotations

import asyncio
import json
import ssl
import subprocess
import threading
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from pathlib import Path
from typing import Any, cast

import structlog
from websockets.exceptions import ConnectionClosed
from websockets.legacy.server import WebSocketServerProtocol, serve

from al1s_terminal.interactive.app_icon import PACKAGE, read_application_icon
from al1s_terminal.interactive.ocr import recognize_crop
from al1s_terminal.interactive.scrcpy_process import ScrcpyConnection, ScrcpyProcessFactory
from al1s_terminal.interactive.scrcpy_wire import validate_control_packet
from al1s_terminal.interactive.sessions import (
    InteractiveChannel,
    InteractiveSessionError,
    InteractiveSessionManager,
)
from al1s_terminal.providers.adb import AdbProvider


class AdbScrcpyRelay:
    """Server-owned scrcpy sockets; no browser access to the ADB server."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        public_url: str,
        process_factory: ScrcpyProcessFactory | None = None,
        adb: AdbProvider | None = None,
        idle_timeout_seconds: float = 1800,
        ocr_model_dir: Path | None = None,
        tls_context: ssl.SSLContext | None = None,
    ) -> None:
        self._ocr_model_dir = ocr_model_dir
        self._ocr_busy = False
        self._host = host
        self._port = port
        self._public_url = public_url.rstrip("/")
        self._factory = process_factory
        self._adb = adb
        self._tls_context = tls_context
        self._streams: dict[str, ScrcpyConnection] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_signal: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()
        self._serving = False
        self._manager = InteractiveSessionManager(
            idle_timeout_seconds=idle_timeout_seconds,
            close_connections=self._close_connections,
        )

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return self._serving
        self._started.clear()
        self._thread = threading.Thread(
            target=self._thread_main,
            daemon=True,
            name="al1s-scrcpy-relay",
        )
        self._thread.start()
        self._started.wait(timeout=5)
        return self._serving

    def create_session(self, serial: str) -> dict[str, Any]:
        if self._factory is None or not self._factory.available():
            raise InteractiveSessionError(
                "scrcpy_asset_unavailable", "Pinned scrcpy asset required"
            )
        if not self.start():
            raise InteractiveSessionError(
                "interactive_relay_unavailable",
                "scrcpy relay failed to start",
            )
        session = self._manager.create(serial)
        try:
            self._prepare_screen_sync(session.token)
        except BaseException:
            self._manager.stop(session.token)
            raise
        base = f"{self._public_url}/scrcpy/{session.token}"
        return {
            "session_token": session.token,
            "device_serial": session.serial,
            "mode": session.mode.value,
            "video_ws_url": f"{base}/video",
            "control_ws_url": f"{base}/control",
            "screenshot_ws_url": f"{base}/screenshot",
            "foreground_ws_url": f"{base}/foreground",
            "app_icon_ws_url": f"{base}/app-icon",
            **({"ocr_ws_url": f"{base}/ocr"} if self._ocr_model_dir else {}),
            "transport": "scrcpy-managed-v1",
            "scrcpy_version": "3.3.4",
        }

    def provider_available(self) -> bool:
        return self._factory is not None and self._factory.available()

    def stop_session(self, token: str) -> bool:
        return self._manager.stop(token)

    def session_mode(self, token: str) -> str:
        return self._manager.get(token).mode.value

    def confirm_platform(self, valid_for_seconds: float) -> None:
        self._manager.confirm_platform(valid_for_seconds)

    def confirmation_epoch(self) -> int:
        return self._manager.confirmation_epoch()

    def confirm_editor(
        self, valid_for_seconds: float, *, expected_epoch: int | None = None,
    ) -> None:
        self._manager.confirm_editor(valid_for_seconds, expected_epoch=expected_epoch)

    def platform_transient_failure(self) -> None:
        self._manager.platform_transient_failure()

    def set_input_authority(self, authority: Callable[[str], bool]) -> None:
        self._manager.set_input_authority(authority)

    def disconnect_platform(self) -> None:
        self._manager.disconnect_platform()

    def begin_automation(self, serial: str) -> None:
        self._manager.begin_automation(serial)

    def end_automation(self, serial: str) -> None:
        self._manager.end_automation(serial)

    def status(self) -> dict[str, Any]:
        sessions = self._manager.snapshots()
        return {
            "available": self._serving and self.provider_available(),
            "sessions": len(sessions),
            "video_connections": sum(item.video_connections for item in sessions),
            "control_connections": sum(item.control_connections for item in sessions),
            "modes": [item.mode.value for item in sessions],
        }

    def stop(self) -> None:
        self._manager.stop_all()
        loop = self._loop
        stop_signal = self._stop_signal
        if loop is not None and stop_signal is not None:
            loop.call_soon_threadsafe(stop_signal.set)
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)

    def _thread_main(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_signal = asyncio.Event()
        try:
            async with serve(
                self._handler,
                self._host,
                self._port,
                max_size=4 * 1024 * 1024,
                max_queue=4,
                compression=None,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=1,
                ssl=self._tls_context,
            ):
                self._serving = True
                self._started.set()
                while not self._stop_signal.is_set():
                    self._manager.expire_platform_confirmation()
                    with suppress(TimeoutError):
                        await asyncio.wait_for(self._stop_signal.wait(), 1)
        finally:
            self._serving = False
            self._started.set()
            self._stop_signal = None
            self._loop = None

    async def _handler(self, websocket: WebSocketServerProtocol, path: str) -> None:
        token, channel = _parse_path(path)
        if token is None or channel is None:
            await websocket.close(code=1008, reason="invalid scrcpy relay path")
            return
        try:
            self._manager.attach(token, channel, websocket)
        except InteractiveSessionError as exc:
            await websocket.close(code=1008, reason=str(exc))
            return
        try:
            if channel is InteractiveChannel.VIDEO:
                await self._video(token, websocket)
            elif channel is InteractiveChannel.SCREENSHOT:
                await self._screenshot(token, websocket)
            elif channel is InteractiveChannel.FOREGROUND:
                await self._foreground(token, websocket)
            elif channel is InteractiveChannel.OCR:
                await self._ocr(websocket)
            elif channel is InteractiveChannel.APP_ICON:
                await self._app_icon(token, websocket)
            else:
                await self._control(token, websocket)
        except ConnectionClosed as exc:
            # Only classify closure; never log capability URLs or native payloads.
            structlog.get_logger().info(
                "scrcpy_channel_closed",
                channel=channel.value,
                code=exc.rcvd.code if exc.rcvd else 1006,
            )
        except (ValueError, InteractiveSessionError):
            await websocket.close(code=1008, reason="scrcpy input rejected or session unavailable")
        except (RuntimeError, OSError, TimeoutError, subprocess.SubprocessError) as exc:
            structlog.get_logger().warning(
                "scrcpy_transport_failed", channel=channel.value, error_type=type(exc).__name__
            )
            await websocket.close(code=1011, reason="scrcpy transport unavailable")
        finally:
            self._manager.detach(token, channel, websocket)

    async def _ocr(self, websocket: WebSocketServerProtocol) -> None:
        if self._ocr_busy or self._ocr_model_dir is None:
            raise RuntimeError("OCR unavailable or busy")
        self._ocr_busy = True
        try:
            body = await asyncio.wait_for(websocket.recv(), 5)
            if not isinstance(body, bytes):
                raise ValueError("OCR requires PNG bytes")
            result = await recognize_crop(body, self._ocr_model_dir)
            await asyncio.wait_for(websocket.send(json.dumps(result).encode()), 3)
        finally:
            self._ocr_busy = False
        await websocket.close()

    async def _video(self, token: str, websocket: WebSocketServerProtocol) -> None:
        if self._factory is None:
            raise RuntimeError("scrcpy provider unavailable")
        await asyncio.to_thread(self._prepare_screen_sync, token)
        opening = asyncio.create_task(self._factory.open(self._manager.get(token).serial))
        disconnected = (
            asyncio.create_task(websocket.wait_closed())
            if hasattr(websocket, "wait_closed")
            else None
        )
        try:
            if disconnected is not None:
                await asyncio.wait({opening, disconnected}, return_when=asyncio.FIRST_COMPLETED)
                if not opening.done():
                    opening.cancel()
                    await asyncio.gather(opening, return_exceptions=True)
                    return
            stream = await opening
        finally:
            if not opening.done():
                opening.cancel()
                await asyncio.gather(opening, return_exceptions=True)
            if disconnected is not None:
                disconnected.cancel()
                await asyncio.gather(disconnected, return_exceptions=True)
        self._streams[token] = stream

        async def reject_upstream() -> None:
            async for _message in websocket:
                await websocket.close(code=1008, reason="video is receive-only")
                return

        async def forward_video() -> None:
            while data := await stream.video.read(64 * 1024):
                await asyncio.wait_for(websocket.send(data), 3)
                self._manager.touch(token)

        tasks = {asyncio.create_task(reject_upstream()), asyncio.create_task(forward_video())}
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._streams.pop(token, None)
            self._manager.disconnect_control(token)
            await stream.close()

    async def _screenshot(self, token: str, websocket: WebSocketServerProtocol) -> None:
        factory = self._factory
        if factory is None:
            raise RuntimeError("screenshot provider unavailable")

        async def reject_upstream() -> None:
            async for _message in websocket:
                raise ValueError("screenshot channel is receive-only")

        async def capture() -> None:
            await asyncio.to_thread(self._prepare_screen_sync, token)
            body = await factory.screenshot(self._manager.get(token).serial)
            self._manager.get(token)  # A revoked session must not receive captured content.
            await asyncio.wait_for(websocket.send(body), 3)

        tasks = {asyncio.create_task(reject_upstream()), asyncio.create_task(capture())}
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            await websocket.close(code=1000, reason="screenshot complete")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _control(self, token: str, websocket: WebSocketServerProtocol) -> None:
        stream = self._streams.get(token)
        if stream is None:
            raise RuntimeError("video must be connected before control")
        async for message in websocket:
            if not isinstance(message, bytes):
                raise ValueError("binary scrcpy control required")
            validate_control_packet(message)
            self._manager.send_control(token, websocket, partial(stream.control.write, message))
            await asyncio.wait_for(stream.control.drain(), 1)

    async def _foreground(self, token: str, websocket: WebSocketServerProtocol) -> None:
        if self._adb is None:
            raise RuntimeError("foreground probe unavailable")
        await asyncio.to_thread(self._prepare_screen_sync, token)
        serial = self._manager.get(token).serial
        package = await asyncio.to_thread(self._adb.foreground_package, serial)
        self._manager.get(token)  # Reject a response after session revocation.
        await asyncio.wait_for(websocket.send(json.dumps({"package_name": package}).encode()), 3)
        await websocket.close(code=1000, reason="foreground probe complete")

    async def _app_icon(self, token: str, websocket: WebSocketServerProtocol) -> None:
        if self._adb is None:
            raise RuntimeError("application icon provider unavailable")
        package_bytes = await asyncio.wait_for(websocket.recv(), 3)
        if not isinstance(package_bytes, bytes) or len(package_bytes) > 255:
            raise ValueError("invalid application icon request")
        package = package_bytes.decode("ascii")
        if not PACKAGE.fullmatch(package):
            raise ValueError("invalid application package")
        serial = self._manager.get(token).serial
        icon = await asyncio.to_thread(read_application_icon, self._adb, serial, package)
        self._manager.get(token)
        await asyncio.wait_for(websocket.send(icon), 3)
        await websocket.close(code=1000, reason="application icon complete")

    def _close_connections(self, connections: tuple[object, ...], reason: str) -> None:
        loop = self._loop
        if loop is None or not connections:
            return

        async def close_all() -> None:
            await asyncio.gather(
                *(
                    cast(WebSocketServerProtocol, connection).close(code=1008, reason=reason)
                    for connection in connections
                ),
                return_exceptions=True,
            )

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is loop:
            loop.create_task(close_all())
        else:
            future = asyncio.run_coroutine_threadsafe(close_all(), loop)
            future.result(timeout=5)

    def _prepare_screen_sync(self, token: str) -> None:
        adb = self._adb
        if adb is not None:
            self._manager.prepare_screen(token, lambda serial: adb.prepare_screen(
                serial, allowed=lambda: self._manager.can_prepare(token),
            ))


def _parse_path(path: str) -> tuple[str | None, InteractiveChannel | None]:
    parts = path.split("?", 1)[0].strip("/").split("/")
    if len(parts) != 3 or parts[0] != "scrcpy":
        return None, None
    try:
        return parts[1], InteractiveChannel(parts[2])
    except ValueError:
        return None, None
