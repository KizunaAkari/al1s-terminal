from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from al1s_terminal.app.editor_coordinator import EditorCoordinator
from al1s_terminal.transport.editor import EditorRequest
from al1s_terminal.transport.platform import PlatformUnavailableError


def setup():
    terminal = uuid4()
    request = EditorRequest(
        session_id=uuid4(),
        terminal_id=terminal,
        device_id=uuid4(),
        status="pending",
        create_deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    platform, secret, discovery, adb, relay = (Mock() for _ in range(5))
    secret.load.return_value = SimpleNamespace(terminal_id=terminal, credential="private")
    platform.pending.return_value = [request]
    platform.report.return_value = request.model_copy(update={"status": "active"})
    discovery.serial_for_target.return_value = "phone"
    relay.create_session.return_value = {
        "transport": "scrcpy-managed-v1",
        "session_token": "token",
        "scrcpy_version": "3.3.4",
        "video_ws_url": "video",
        "control_ws_url": "control",
    }
    coordinator = EditorCoordinator(platform, secret, discovery, adb, relay)
    return coordinator, platform, relay, request


def test_confirmation_retry_preserves_same_token():
    coordinator, platform, relay, _request = setup()
    platform.report.side_effect = PlatformUnavailableError(503, "offline", "offline")
    with pytest.raises(PlatformUnavailableError):
        coordinator.reconcile()
    platform.report.side_effect = None
    coordinator.reconcile()
    assert relay.create_session.call_count == 1
    assert platform.report.call_args.args[-1]["session_token"] == "token"


def test_optional_native_screenshot_capability_is_reported():
    coordinator, platform, relay, _request = setup()
    relay.create_session.return_value["screenshot_ws_url"] = "wss://phone/scrcpy/token/screenshot"
    coordinator.reconcile()
    assert platform.report.call_args.args[-1]["screenshot_ws_url"].endswith("/screenshot")


def test_restart_never_recreates_old_active_session():
    coordinator, platform, relay, request = setup()
    platform.pending.return_value = [request.model_copy(update={"status": "active"})]
    coordinator.reconcile()
    relay.create_session.assert_not_called()
    assert platform.report.call_args.args[3] == "closed"


def test_close_revokes_before_reporting_and_late_activation_is_destroyed():
    coordinator, platform, relay, request = setup()
    platform.report.return_value = request.model_copy(update={"status": "expired"})
    coordinator.reconcile()
    relay.stop_session.assert_called_once_with("token")
    assert not coordinator._connections


def test_expired_request_never_opens_phone_session():
    coordinator, platform, relay, request = setup()
    platform.pending.return_value = [
        request.model_copy(update={"create_deadline": datetime.now(UTC) - timedelta(seconds=1)})
    ]
    coordinator.reconcile()
    relay.create_session.assert_not_called()
    assert platform.report.call_args.args[3] == "closed"


def test_cancelled_pending_missing_from_list_still_revokes_local_token():
    coordinator, platform, relay, request = setup()
    platform.report.side_effect = PlatformUnavailableError(503, "offline", "offline")
    with pytest.raises(PlatformUnavailableError):
        coordinator.reconcile()
    platform.pending.return_value = []
    platform.report.side_effect = None
    platform.report.return_value = request.model_copy(update={"status": "closed"})
    coordinator.reconcile()
    relay.stop_session.assert_called_once_with("token")
    assert not coordinator._connections
    assert not coordinator._requests


def test_fast_polling_does_not_repeat_active_writes_but_close_is_immediate():
    coordinator, platform, relay, request = setup()
    coordinator.reconcile()
    platform.pending.return_value = [request.model_copy(update={"status": "active"})]
    for _ in range(10):
        coordinator.reconcile()
    assert platform.report.call_count == 1
    platform.pending.return_value = [request.model_copy(update={"status": "closing"})]
    coordinator.reconcile()
    relay.stop_session.assert_called_once_with("token")
    assert platform.report.call_args.args[3] == "closed"
