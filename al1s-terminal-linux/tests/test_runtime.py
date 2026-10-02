from __future__ import annotations

from concurrent.futures import Future
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

import pytest

from al1s_terminal.app.lifecycle import RegistrationRequiredError
from al1s_terminal.app.runtime import TerminalRuntime
from al1s_terminal.transport.platform import PlatformUnavailableError


class _OfflineLifecycle:
    def __init__(self, *, registered: bool) -> None:
        self.registered = registered
        self.require_calls = 0

    def require_registered_identity(self) -> Any:
        self.require_calls += 1
        if not self.registered:
            raise RegistrationRequiredError("terminal is not registered")
        return SimpleNamespace(terminal_id="terminal-1")


def _runtime_with_bootstrap(*, registered: bool, offline: bool) -> tuple[TerminalRuntime, Any]:
    runtime = object.__new__(TerminalRuntime)
    lifecycle = _OfflineLifecycle(registered=registered)
    mutable = cast(Any, runtime)
    mutable._lifecycle = lifecycle

    def bootstrap() -> dict[str, object]:
        if offline:
            raise PlatformUnavailableError(503, "platform_unavailable", "offline")
        return {}

    mutable.bootstrap = bootstrap
    return runtime, lifecycle


def test_runtime_bootstrap_continues_offline_with_registered_identity() -> None:
    runtime, lifecycle = _runtime_with_bootstrap(registered=True, offline=True)

    runtime._bootstrap_for_run()

    assert lifecycle.require_calls == 1


def test_runtime_bootstrap_does_not_allow_unregistered_offline_start() -> None:
    runtime, lifecycle = _runtime_with_bootstrap(registered=False, offline=True)

    with pytest.raises(RegistrationRequiredError, match="not registered"):
        runtime._bootstrap_for_run()

    assert lifecycle.require_calls == 1


def test_runtime_bootstrap_does_not_use_offline_fallback_when_platform_is_available() -> None:
    runtime, lifecycle = _runtime_with_bootstrap(registered=True, offline=False)

    runtime._bootstrap_for_run()

    assert lifecycle.require_calls == 0


def test_completed_execution_schedules_reports_without_synchronous_upload() -> None:
    runtime = object.__new__(TerminalRuntime)
    mutable = cast(Any, runtime)
    future: Future[Any] = Future()
    future.set_result(SimpleNamespace(disposition="result_queued"))
    mutable._execution_future = future
    mutable._interactive_relay = Mock()
    mutable._delivery = Mock()
    mutable._artifact_upload = Mock()
    mutable._reconciliation_requested = Mock()
    mutable._delivery.flush_outbox.side_effect = PlatformUnavailableError(
        503, "platform_unavailable", "offline"
    )

    assert runtime._poll_execution() == 0.25
    mutable._reconciliation_requested.set.assert_called_once_with()
    mutable._delivery.flush_outbox.assert_not_called()
    mutable._artifact_upload.flush.assert_not_called()
    assert mutable._execution_future is None


def test_control_revocation_without_video_provider_is_safe() -> None:
    runtime = object.__new__(TerminalRuntime)
    cast(Any, runtime)._interactive_relay = None
    runtime._revoke_manual_control()


def test_runtime_slow_presence_does_not_stall_heartbeat_or_editor(monkeypatch):
    import time
    from threading import Event, Lock

    from al1s_terminal.app import runtime as module
    from al1s_terminal.app.background import SingleFlight

    runtime = object.__new__(TerminalRuntime)
    runtime._settings = SimpleNamespace(
        heartbeat_interval_seconds=1, reconciliation_interval_seconds=1, gc_interval_seconds=1000
    )
    runtime._shutdown_requested = Event()
    runtime._reconciliation_requested = Event()
    runtime._lifecycle_lock = Lock()
    runtime._bootstrap_for_run = Mock()
    runtime._poll_execution = Mock(return_value=0.25)
    runtime._platform = Mock()
    runtime._secret_store = Mock()
    runtime._adb = Mock()
    runtime._interactive_relay = Mock()
    runtime._lifecycle = Mock()
    runtime._delivery = Mock()
    runtime._artifact_upload = Mock()
    runtime._device_discovery = Mock()
    runtime._mqtt_hints = Mock()
    runtime._io_workers = {
        name: SingleFlight("test-" + name)
        for name in (
            "editor",
            "heartbeat",
            "capability",
            "presence",
            "cancel",
            "quick-control",
            "quick-events",
            "mqtt",
            "gc",
        )
    }
    runtime._delivery_worker = SingleFlight("test-delivery")
    runtime._media_worker = SingleFlight("test-media")
    release, entered = Event(), Event()

    def slow_presence():
        entered.set()
        release.wait(3)

    runtime._device_discovery.report_presence.side_effect = slow_presence
    editor = Mock()
    from itertools import chain, repeat

    editor.reconcile.side_effect = chain([ValueError("bad response")], repeat(None))
    monkeypatch.setattr(module, "EditorCoordinator", lambda *args: editor)
    virtual = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: virtual[0])

    def tick(*args):
        virtual[0] += 0.25
        time.sleep(0.002)

    monkeypatch.setattr(module, "_interruptible_wait", tick)
    try:
        runtime.run(stop_requested=lambda: virtual[0] >= 20)
        assert entered.is_set()
        assert runtime._device_discovery.report_presence.call_count == 1
        assert runtime._lifecycle.heartbeat.call_count >= 15
        assert editor.reconcile.call_count >= 15
        assert runtime._delivery.poll_quick_test_stop.call_count >= 8
    finally:
        release.set()
        for worker in [
            *runtime._io_workers.values(),
            runtime._delivery_worker,
            runtime._media_worker,
        ]:
            worker.close()
