from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog

from al1s_terminal.app.device_discovery import TargetDeviceDiscoveryService
from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.interactive.relay import AdbScrcpyRelay
from al1s_terminal.interactive.sessions import InteractiveSessionError
from al1s_terminal.providers.adb import AdbProvider, AdbProviderError
from al1s_terminal.transport.editor import EditorPlatformClient, EditorRequest


class EditorCoordinator:
    """Short-lived state only: after restart never reconstruct a former capability."""

    def __init__(
        self,
        platform: EditorPlatformClient,
        secrets: FileSecretStore,
        discovery: TargetDeviceDiscoveryService,
        adb: AdbProvider,
        relay: AdbScrcpyRelay | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._platform, self._secrets, self._discovery = platform, secrets, discovery
        self._adb, self._relay, self._clock = adb, relay, clock
        self._instance = uuid4()
        self._connections: dict[UUID, dict[str, str]] = {}
        self._requests: dict[UUID, EditorRequest] = {}
        self._reported: dict[UUID, datetime] = {}

    def reconcile(self) -> None:
        identity = self._secrets.load()
        if identity is None:
            return
        requests = self._platform.pending(identity.credential)
        seen = {request.session_id for request in requests}
        # A cancelled pending request disappears from the bounded work list.
        # Confirm local capabilities individually, never infer closure from a page.
        requests.extend(request for key, request in self._requests.items() if key not in seen)
        for request in requests:
            if request.terminal_id != identity.terminal_id:
                raise ValueError("Editor request belongs to another terminal")
            self._reconcile_one(identity.credential, request)

    def _reconcile_one(self, credential: str, request: EditorRequest) -> None:
        connection = self._connections.get(request.session_id)
        if request.status == "closing" or (
            request.status == "pending" and self._clock() >= request.create_deadline
        ):
            self._close(request.session_id)
            self._platform.report(credential, request, self._instance, "closed")
            return
        if request.status == "active" and connection is None:
            self._platform.report(credential, request, self._instance, "closed")
            return
        if connection is None:
            try:
                if self._relay is None:
                    raise RuntimeError("Editor unavailable")
                serial = self._discovery.serial_for_target(request.device_id)
                if serial is None:
                    raise RuntimeError("Editor device unavailable")
                self._adb.require_device(serial)
                created = self._relay.create_session(serial)
                connection = {
                    key: str(created[key])
                    for key in (
                        "transport",
                        "scrcpy_version",
                        "session_token",
                        "video_ws_url",
                        "control_ws_url",
                    )
                }
                if "screenshot_ws_url" in created:
                    connection["screenshot_ws_url"] = str(created["screenshot_ws_url"])
                if "foreground_ws_url" in created:
                    connection["foreground_ws_url"] = str(created["foreground_ws_url"])
                if "ocr_ws_url" in created:
                    connection["ocr_ws_url"] = str(created["ocr_ws_url"])
                if "app_icon_ws_url" in created:
                    connection["app_icon_ws_url"] = str(created["app_icon_ws_url"])
                self._connections[request.session_id] = connection
                self._requests[request.session_id] = request
            except (RuntimeError, AdbProviderError) as exc:
                structlog.get_logger().warning(
                    "editor_session_setup_failed",
                    session_id=str(request.session_id),
                    error_type=type(exc).__name__,
                    relay_enabled=self._relay is not None,
                )
                self._platform.report(credential, request, self._instance, "failed")
                return
        elif self._relay is not None:
            try:
                self._relay.session_mode(connection["session_token"])
            except InteractiveSessionError:
                self._close(request.session_id)
                self._platform.report(credential, request, self._instance, "closed")
                return
        reported = self._reported.get(request.session_id)
        if (
            request.status == "active"
            and reported
            and (self._clock() - reported).total_seconds() < 10
        ):
            return
        reply = self._platform.report(credential, request, self._instance, "active", connection)
        self._reported[request.session_id] = self._clock()
        if reply.status != "active":
            self._close(request.session_id)

    def _close(self, session_id: UUID) -> None:
        self._reported.pop(session_id, None)
        self._requests.pop(session_id, None)
        connection = self._connections.pop(session_id, None)
        if connection is not None and self._relay is not None:
            self._relay.stop_session(connection["session_token"])
