import asyncio
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

try:
    from websockets.server import WebSocketServerProtocol, serve
except ImportError:  # Allows diagnostics before terminal requirements are installed.
    WebSocketServerProtocol = Any
    serve = None


@dataclass
class InteractiveSession:
    token: str
    serial: str
    created_at: float
    last_activity: float
    connections: set[Any] = field(default_factory=set)
    bytes_to_adb: int = 0
    bytes_to_browser: int = 0


class AdbWebSocketRelay:
    """Short-lived authenticated WebSocket relay to the local ADB server.

    The browser runs the scrcpy client and hardware video decoder. The terminal
    only copies bytes between WebSocket connections and 127.0.0.1:5037.
    """

    def __init__(
        self,
        host: str,
        port: int,
        public_url: str,
        adb_host: str = "127.0.0.1",
        adb_port: int = 5037,
        idle_timeout_seconds: int = 1200,
    ):
        self.host = host
        self.port = port
        self.public_url = public_url.rstrip("/")
        self.adb_host = adb_host
        self.adb_port = adb_port
        self.idle_timeout_seconds = idle_timeout_seconds
        self.available = serve is not None
        self._sessions: dict[str, InteractiveSession] = {}
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._started = threading.Event()
        self._serving = False
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        if not self.available:
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="adb-websocket-relay")
        self._thread.start()
        self._started.wait(timeout=5)
        return self._serving

    def _thread_main(self):
        asyncio.run(self._serve())

    async def _serve(self):
        self._loop = asyncio.get_running_loop()
        try:
            async with serve(
                self._handler,
                self.host,
                self.port,
                max_size=None,
                max_queue=32,
                compression=None,
                ping_interval=20,
                ping_timeout=20,
            ):
                self._serving = True
                self._started.set()
                await asyncio.Future()
        finally:
            self._serving = False
            self._started.set()

    def create_session(self, serial: str) -> dict[str, Any]:
        if not self.start():
            raise RuntimeError("WebSocket relay is unavailable; install agent requirements and restart the service")
        self.stop_all()
        now = time.monotonic()
        token = secrets.token_urlsafe(32)
        session = InteractiveSession(token=token, serial=serial, created_at=now, last_activity=now)
        with self._lock:
            self._sessions[token] = session
        return {
            "session_token": token,
            "device_serial": serial,
            "ws_url": f"{self.public_url}/adb/{token}",
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "transport": "adb-server-websocket",
        }

    def stop(self, token: str) -> bool:
        with self._lock:
            session = self._sessions.pop(token, None)
        if not session:
            return False
        self._close_connections(session.connections)
        return True

    def stop_all(self):
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            self._close_connections(session.connections)

    def _close_connections(self, connections: set[Any]):
        loop = self._loop
        if not loop or not connections:
            return

        async def close_all():
            await asyncio.gather(
                *(connection.close(code=1001, reason="interactive session ended") for connection in list(connections)),
                return_exceptions=True,
            )

        future = asyncio.run_coroutine_threadsafe(close_all(), loop)
        try:
            future.result(timeout=2)
        except Exception:
            pass

    def status(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            sessions = list(self._sessions.values())
            return {
                "available": self.available and self._serving,
                "active": bool(sessions),
                "sessions": len(sessions),
                "connections": sum(len(item.connections) for item in sessions),
                "bytes_to_adb": sum(item.bytes_to_adb for item in sessions),
                "bytes_to_browser": sum(item.bytes_to_browser for item in sessions),
                "age_seconds": round(now - sessions[0].created_at, 1) if sessions else 0,
            }

    def _get_session(self, token: str) -> InteractiveSession | None:
        now = time.monotonic()
        with self._lock:
            session = self._sessions.get(token)
            if not session:
                return None
            if not session.connections and now - session.last_activity > self.idle_timeout_seconds:
                self._sessions.pop(token, None)
                return None
            session.last_activity = now
            return session

    async def _handler(self, websocket: WebSocketServerProtocol, path: str):
        prefix = "/adb/"
        token = path[len(prefix):] if path.startswith(prefix) else ""
        session = self._get_session(token)
        if not session:
            await websocket.close(code=1008, reason="invalid or expired interactive session")
            return

        try:
            reader, writer = await asyncio.open_connection(self.adb_host, self.adb_port)
        except OSError as exc:
            await websocket.close(code=1011, reason=f"ADB server unavailable: {exc}")
            return

        with self._lock:
            session.connections.add(websocket)

        async def browser_to_adb():
            async for message in websocket:
                if isinstance(message, str):
                    raise ValueError("ADB relay accepts binary WebSocket messages only")
                writer.write(message)
                await writer.drain()
                with self._lock:
                    session.bytes_to_adb += len(message)
                    session.last_activity = time.monotonic()

        async def adb_to_browser():
            while True:
                data = await reader.read(64 * 1024)
                if not data:
                    break
                await websocket.send(data)
                with self._lock:
                    session.bytes_to_browser += len(data)
                    session.last_activity = time.monotonic()

        tasks = {asyncio.create_task(browser_to_adb()), asyncio.create_task(adb_to_browser())}
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        finally:
            writer.close()
            await writer.wait_closed()
            with self._lock:
                session.connections.discard(websocket)
                session.last_activity = time.monotonic()
