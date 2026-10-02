from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class InteractiveSessionMode(StrEnum):
    CONTROL = "control"
    VIEW_ONLY = "view_only"


class InteractiveChannel(StrEnum):
    VIDEO = "video"
    CONTROL = "control"
    SCREENSHOT = "screenshot"
    FOREGROUND = "foreground"
    APP_ICON = "app-icon"
    OCR = "ocr"


class InteractiveSessionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class InteractiveAutomationGate(Protocol):
    def begin_automation(self, serial: str) -> None: ...

    def end_automation(self, serial: str) -> None: ...


@dataclass(frozen=True, slots=True)
class InteractiveSessionSnapshot:
    token: str
    serial: str
    mode: InteractiveSessionMode
    created_at: float
    last_activity: float
    video_connections: int
    control_connections: int


@dataclass(slots=True)
class _InteractiveSession:
    token: str
    serial: str
    created_at: float
    last_activity: float
    mode: InteractiveSessionMode = InteractiveSessionMode.CONTROL
    video_connections: set[object] = field(default_factory=set)
    control_connections: set[object] = field(default_factory=set)
    screenshot_connections: set[object] = field(default_factory=set)
    foreground_connections: set[object] = field(default_factory=set)
    app_icon_connections: set[object] = field(default_factory=set)
    ocr_connections: set[object] = field(default_factory=set)
    last_screenshot: float = float("-inf")

    def connections(self, channel: InteractiveChannel) -> set[object]:
        return {
            InteractiveChannel.VIDEO: self.video_connections,
            InteractiveChannel.CONTROL: self.control_connections,
            InteractiveChannel.SCREENSHOT: self.screenshot_connections,
            InteractiveChannel.FOREGROUND: self.foreground_connections,
            InteractiveChannel.APP_ICON: self.app_icon_connections,
            InteractiveChannel.OCR: self.ocr_connections,
        }[channel]


ConnectionCloser = Callable[[tuple[object, ...], str], None]


class InteractiveSessionManager:
    """Owns scrcpy session modes independently from WebSocket transport details."""

    def __init__(
        self,
        *,
        idle_timeout_seconds: float = 1800,
        clock: Callable[[], float] | None = None,
        close_connections: ConnectionCloser | None = None,
    ) -> None:
        if idle_timeout_seconds <= 0:
            raise ValueError("idle_timeout_seconds must be positive")
        self._idle_timeout_seconds = idle_timeout_seconds
        self._clock = clock or time.monotonic
        self._close_connections = close_connections or (lambda _connections, _reason: None)
        self._sessions: dict[str, _InteractiveSession] = {}
        self._automation_serial: str | None = None
        self._platform_valid_until = 0.0
        self._lock = threading.RLock()

    def confirm_platform(self, valid_for_seconds: float) -> None:
        if valid_for_seconds <= 0:
            raise ValueError("platform confirmation lifetime must be positive")
        # Expired confirmations must revoke old connections before restoring modes.
        self.expire_platform_confirmation()
        with self._lock:
            self._platform_valid_until = self._clock() + valid_for_seconds
            for session in self._sessions.values():
                session.mode = self._mode_for(session.serial)

    def disconnect_platform(self) -> None:
        with self._lock:
            self._platform_valid_until = 0.0
            connections = tuple(c for s in self._sessions.values() for c in s.control_connections)
            for session in self._sessions.values():
                session.mode = InteractiveSessionMode.VIEW_ONLY
                session.control_connections.clear()
        if connections:
            self._close_connections(connections, "platform connection unavailable")

    def expire_platform_confirmation(self) -> None:
        with self._lock:
            if self._clock() < self._platform_valid_until:
                return
            connections = tuple(c for s in self._sessions.values() for c in s.control_connections)
            for session in self._sessions.values():
                session.mode = InteractiveSessionMode.VIEW_ONLY
                session.control_connections.clear()
        if connections:
            self._close_connections(connections, "platform confirmation expired")

    def send_control(self, token: str, connection: object, send: Callable[[], None]) -> None:
        """Authorize and enqueue one bounded input atomically with revocation."""
        with self._lock:
            session = self._live_session(token)
            if (
                self._mode_for(session.serial) is InteractiveSessionMode.VIEW_ONLY
                or connection not in session.control_connections
            ):
                raise InteractiveSessionError("interactive_control_disabled", "Control unavailable")
            send()
            session.last_activity = self._clock()

    def _mode_for(self, serial: str) -> InteractiveSessionMode:
        if self._clock() >= self._platform_valid_until or self._automation_serial == serial:
            return InteractiveSessionMode.VIEW_ONLY
        return InteractiveSessionMode.CONTROL

    def create(self, serial: str) -> InteractiveSessionSnapshot:
        normalized = serial.strip()
        if not normalized:
            raise InteractiveSessionError("interactive_serial_missing", "ADB serial is required")
        now = self._clock()
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._discard_expired()
            if self._sessions:
                raise InteractiveSessionError(
                    "interactive_device_busy", "An editor session is already active"
                )
            session = _InteractiveSession(
                token=token,
                serial=normalized,
                created_at=now,
                last_activity=now,
                mode=self._mode_for(normalized),
            )
            self._sessions[token] = session
        return self._snapshot(session)

    def stop(self, token: str) -> bool:
        with self._lock:
            session = self._sessions.pop(token, None)
        if session is None:
            return False
        self._close_connections(
            tuple((*session.video_connections, *session.control_connections,
                   *session.screenshot_connections, *session.foreground_connections,
                   *session.app_icon_connections, *session.ocr_connections)),
            "interactive session ended",
        )
        return True

    def disconnect_control(self, token: str) -> None:
        """A replacement video process must never inherit old input connections."""
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return
            connections = tuple(session.control_connections)
            session.control_connections.clear()
        self._close_connections(connections, "video transport restarting")

    def stop_all(self) -> None:
        with self._lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
        connections = tuple(
            connection
            for item in sessions
            for connection in (*item.video_connections, *item.control_connections,
                               *item.screenshot_connections, *item.foreground_connections,
                               *item.app_icon_connections, *item.ocr_connections)
        )
        if connections:
            self._close_connections(connections, "interactive service stopped")

    def attach(self, token: str, channel: InteractiveChannel, connection: object) -> None:
        with self._lock:
            session = self._live_session(token)
            if channel is InteractiveChannel.CONTROL and (
                session.mode is InteractiveSessionMode.VIEW_ONLY
                or self._mode_for(session.serial) is InteractiveSessionMode.VIEW_ONLY
            ):
                raise InteractiveSessionError(
                    "interactive_control_disabled",
                    "scrcpy control is disabled while automation is running",
                )
            target = session.connections(channel)
            if target:
                raise InteractiveSessionError(
                    "interactive_channel_busy", "Channel already attached"
                )
            if channel is InteractiveChannel.SCREENSHOT:
                if self._clock() - session.last_screenshot < 1:
                    raise InteractiveSessionError("screenshot_rate_limit", "Capture too frequent")
                session.last_screenshot = self._clock()
            target.add(connection)
            session.last_activity = self._clock()

    def detach(self, token: str, channel: InteractiveChannel, connection: object) -> None:
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return
            target = session.connections(channel)
            target.discard(connection)
            session.last_activity = self._clock()

    def touch(self, token: str) -> None:
        with self._lock:
            self._live_session(token).last_activity = self._clock()

    def begin_automation(self, serial: str) -> None:
        normalized = serial.strip()
        with self._lock:
            if self._automation_serial not in (None, normalized):
                raise InteractiveSessionError(
                    "interactive_device_busy",
                    "another target device already owns the automation gate",
                )
            self._automation_serial = normalized
            control_connections: list[object] = []
            for session in self._sessions.values():
                if session.serial != normalized:
                    continue
                session.mode = InteractiveSessionMode.VIEW_ONLY
                control_connections.extend(session.control_connections)
                session.control_connections.clear()
        if control_connections:
            self._close_connections(
                tuple(control_connections),
                "scrcpy control disabled while automation is running",
            )

    def end_automation(self, serial: str) -> None:
        normalized = serial.strip()
        with self._lock:
            if self._automation_serial != normalized:
                return
            self._automation_serial = None
            for session in self._sessions.values():
                if session.serial == normalized:
                    session.mode = self._mode_for(session.serial)

    def get(self, token: str) -> InteractiveSessionSnapshot:
        self.expire_platform_confirmation()
        with self._lock:
            return self._snapshot(self._live_session(token))

    def snapshots(self) -> tuple[InteractiveSessionSnapshot, ...]:
        self.expire_platform_confirmation()
        with self._lock:
            self._discard_expired()
            return tuple(self._snapshot(item) for item in self._sessions.values())

    def _live_session(self, token: str) -> _InteractiveSession:
        self._discard_expired()
        session = self._sessions.get(token)
        if session is None:
            raise InteractiveSessionError(
                "interactive_session_invalid",
                "interactive session is invalid or expired",
            )
        return session

    def _discard_expired(self) -> None:
        now = self._clock()
        expired = [
            token
            for token, session in self._sessions.items()
            if not session.video_connections
            and not session.control_connections
            and not session.screenshot_connections
            and not session.foreground_connections
            and now - session.last_activity > self._idle_timeout_seconds
        ]
        for token in expired:
            self._sessions.pop(token, None)

    @staticmethod
    def _snapshot(session: _InteractiveSession) -> InteractiveSessionSnapshot:
        return InteractiveSessionSnapshot(
            token=session.token,
            serial=session.serial,
            mode=session.mode,
            created_at=session.created_at,
            last_activity=session.last_activity,
            video_connections=len(session.video_connections),
            control_connections=len(session.control_connections),
        )
