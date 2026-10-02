from __future__ import annotations

import pytest

from al1s_terminal.interactive.relay import AdbScrcpyRelay, _parse_path
from al1s_terminal.interactive.sessions import (
    InteractiveChannel,
    InteractiveSessionError,
    InteractiveSessionManager,
    InteractiveSessionMode,
)


def test_automation_keeps_video_and_revokes_control_channel() -> None:
    closed: list[tuple[tuple[object, ...], str]] = []
    manager = InteractiveSessionManager(
        close_connections=lambda connections, reason: closed.append((connections, reason))
    )
    session = manager.create("phone-1")
    manager.confirm_platform(30)
    video = object()
    control = object()
    manager.attach(session.token, InteractiveChannel.VIDEO, video)
    manager.attach(session.token, InteractiveChannel.CONTROL, control)

    manager.begin_automation("phone-1")

    active = manager.get(session.token)
    assert active.mode is InteractiveSessionMode.VIEW_ONLY
    assert active.video_connections == 1
    assert active.control_connections == 0
    assert closed == [((control,), "scrcpy control disabled while automation is running")]
    with pytest.raises(InteractiveSessionError) as exc_info:
        manager.attach(session.token, InteractiveChannel.CONTROL, object())
    assert exc_info.value.code == "interactive_control_disabled"

    manager.end_automation("phone-1")
    replacement_control = object()
    manager.attach(session.token, InteractiveChannel.CONTROL, replacement_control)
    restored = manager.get(session.token)
    assert restored.mode is InteractiveSessionMode.CONTROL
    assert restored.video_connections == 1
    assert restored.control_connections == 1


def test_duplicate_session_does_not_replace_previous_capability() -> None:
    closed: list[tuple[tuple[object, ...], str]] = []
    manager = InteractiveSessionManager(
        close_connections=lambda connections, reason: closed.append((connections, reason))
    )
    first = manager.create("phone-1")
    manager.confirm_platform(30)
    video = object()
    control = object()
    manager.attach(first.token, InteractiveChannel.VIDEO, video)
    manager.attach(first.token, InteractiveChannel.CONTROL, control)

    with pytest.raises(InteractiveSessionError):
        manager.create("phone-1")
    assert manager.get(first.token).video_connections == 1
    assert not closed
    manager.stop(first.token)
    assert manager.create("phone-1").token != first.token
    assert set(closed[0][0]) == {video, control}


def test_idle_session_expires_only_without_open_channels() -> None:
    now = [10.0]
    manager = InteractiveSessionManager(
        idle_timeout_seconds=5,
        clock=lambda: now[0],
    )
    session = manager.create("phone-1")
    video = object()
    manager.attach(session.token, InteractiveChannel.VIDEO, video)
    now[0] = 20.0
    assert manager.get(session.token).video_connections == 1

    manager.detach(session.token, InteractiveChannel.VIDEO, video)
    now[0] = 26.0
    with pytest.raises(InteractiveSessionError) as exc_info:
        manager.get(session.token)
    assert exc_info.value.code == "interactive_session_invalid"


def test_default_idle_timeout_is_thirty_minutes() -> None:
    now = [0.0]
    manager = InteractiveSessionManager(clock=lambda: now[0])
    session = manager.create("phone-1")
    now[0] = 1201.0
    assert manager.get(session.token).serial == "phone-1"
    now[0] = 1801.0
    with pytest.raises(InteractiveSessionError):
        manager.get(session.token)


def test_relay_path_requires_explicit_video_or_control_channel() -> None:
    assert _parse_path("/scrcpy/token/video") == ("token", InteractiveChannel.VIDEO)
    assert _parse_path("/scrcpy/token/control?source=editor") == (
        "token",
        InteractiveChannel.CONTROL,
    )
    assert _parse_path("/scrcpy/token") == (None, None)
    assert _parse_path("/scrcpy/token/input") == (None, None)


def test_relay_server_can_stop_without_leaving_listener_thread() -> None:
    relay = AdbScrcpyRelay(
        host="127.0.0.1",
        port=0,
        public_url="ws://terminal.local:8766",
    )

    assert relay.start() is True
    assert relay.status()["available"] is False  # No pinned server configured.
    relay.stop()
    assert relay.status()["available"] is False


def test_platform_disconnect_keeps_video_and_requires_fresh_control() -> None:
    closed = []
    manager = InteractiveSessionManager(close_connections=lambda c, r: closed.extend(c))
    session = manager.create("phone")
    assert session.mode is InteractiveSessionMode.VIEW_ONLY
    video, old, new = object(), object(), object()
    manager.attach(session.token, InteractiveChannel.VIDEO, video)
    with pytest.raises(InteractiveSessionError):
        manager.attach(session.token, InteractiveChannel.CONTROL, old)
    manager.confirm_platform(30)
    manager.attach(session.token, InteractiveChannel.CONTROL, old)
    sent = []
    manager.send_control(session.token, old, lambda: sent.append("first"))
    manager.disconnect_platform()
    assert closed == [old]
    assert manager.get(session.token).video_connections == 1
    manager.begin_automation("phone")
    manager.end_automation("phone")
    assert manager.get(session.token).mode is InteractiveSessionMode.VIEW_ONLY
    manager.confirm_platform(30)
    with pytest.raises(InteractiveSessionError):
        manager.send_control(session.token, old, lambda: sent.append("stale"))
    manager.attach(session.token, InteractiveChannel.CONTROL, new)
    manager.send_control(session.token, new, lambda: sent.append("new"))
    assert sent == ["first", "new"]


def test_expired_confirmation_rejects_input_even_without_watchdog():
    now = [1.0]
    manager = InteractiveSessionManager(clock=lambda: now[0])
    manager.confirm_platform(3)
    session = manager.create("phone")
    control = object()
    manager.attach(session.token, InteractiveChannel.CONTROL, control)
    now[0] = 4.0
    with pytest.raises(InteractiveSessionError):
        manager.send_control(session.token, control, lambda: pytest.fail("unexpected send"))
    manager.confirm_platform(3)
    with pytest.raises(InteractiveSessionError):
        manager.send_control(session.token, control, lambda: pytest.fail("stale connection"))


def test_video_replacement_revokes_old_control_without_revoking_lease():
    closed = []
    manager = InteractiveSessionManager(close_connections=lambda c, r: closed.extend(c))
    manager.confirm_platform(30)
    session = manager.create("phone")
    old = object()
    manager.attach(session.token, InteractiveChannel.CONTROL, old)
    manager.disconnect_control(session.token)
    assert closed == [old]
    with pytest.raises(InteractiveSessionError):
        manager.send_control(session.token, old, lambda: pytest.fail("stale input"))
    new = object()
    manager.attach(session.token, InteractiveChannel.CONTROL, new)
    sent = []
    manager.send_control(session.token, new, lambda: sent.append(1))
    assert sent == [1]
