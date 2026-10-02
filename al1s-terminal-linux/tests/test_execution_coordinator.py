from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine

from al1s_terminal.app.delivery import DeliveryCoordinator
from al1s_terminal.app.execution_coordinator import ExecutionCoordinator
from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.execution.content_store import ContentAddressedStore
from al1s_terminal.execution.maa_pipeline import CompiledMaaTask
from al1s_terminal.execution.maa_plan_compiler import MaaPlanCompiler
from al1s_terminal.execution.maa_runtime import MaaExecutionEngine, MaaRuntimeError
from al1s_terminal.execution.work_item_loader import ExecutionWorkLoader
from al1s_terminal.interactive.sessions import InteractiveSessionError
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.providers.adb import AdbDevice
from al1s_terminal.transport.delivery_models import (
    AttemptResultPayload,
    AttemptResultReceiptPayload,
    AttemptStartPayload,
    AttemptStartResultPayload,
    QuickTestControlPayload,
    QuickTestEventBatchPayload,
    QuickTestEventReceiptPayload,
    QuickTestResultPayload,
    QuickTestResultReceiptPayload,
)
from al1s_terminal.transport.platform import PlatformError, PlatformUnavailableError
from al1s_terminal.types import (
    AdbDeviceState,
    OutboxStatus,
    SecureIdentity,
    WorkItemKind,
    WorkItemStatus,
)

NOW = datetime(2026, 8, 31, 21, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "code,retryable",
    [
        ("maa_pipeline_failed", True),
        ("post_assertion_failed", True),
        ("execution_cancelled", False),
        ("execution_timeout", False),
        ("maa_screen_size_mismatch", False),
        ("maa_runtime_failed", False),
    ],
)
def test_final_failure_report_has_retry_classification_and_absent_capture_state(
    local_engine,
    tmp_path,
    code,
    retryable,
):
    _formal_work(local_engine, uuid4())

    class Runner(FakeMaaRunner):
        def run(self, *args, **kwargs):
            raise MaaRuntimeError(code, "test failure")

    result = _coordinator(local_engine, tmp_path, FakePlatform(), uuid4(), Runner()).run_once()
    assert result.disposition == "result_queued"
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        reports = uow.outbox.claim_batch(
            worker_id="inspect", now=NOW, lease_duration=timedelta(seconds=1), limit=10
        )
        payload = reports[0].payload
        assert payload["retryable"] is retryable
        assert payload["diagnostic"]["failure_screenshot_error"] == "capture_not_generated"


def _definition() -> dict[str, object]:
    return {
        "schema_version": 1,
        "definition_type": "script",
        "entry_definition_key": "main",
        "definitions": {
            "main": {
                "schema_version": 1,
                "compiler_version": "maa-registered-actions-v1",
                "script_version_id": str(uuid4()),
                "script_name": "测试脚本",
                "script_type": "standard",
                "target": {},
                "steps": [
                    {
                        "step_index": 1,
                        "action_id": "wait",
                        "handler_id": "maa.pipeline.wait",
                        "parameters": {"seconds": 0},
                        "wrappers": [],
                    }
                ],
                "independent_rules": [],
                "cleanup_on_finish": False,
            }
        },
    }


class FakePlatform:
    def __init__(self) -> None:
        self.start_calls: list[AttemptStartPayload] = []
        self.result_calls: list[AttemptResultPayload] = []
        self.quick_result_calls: list[QuickTestResultPayload] = []
        self.start_error: PlatformUnavailableError | None = None
        self.execution_events: list[tuple[str, UUID]] = []
        self.quick_stop_requested = False
        self.quick_event_batches: list[QuickTestEventBatchPayload] = []
        self.quick_event_error: PlatformError | None = None

    def get_quick_test_control(
        self, _terminal_id: UUID, _credential: str, _session_id: UUID
    ) -> QuickTestControlPayload:
        return QuickTestControlPayload(status="claimed", cancel_requested=self.quick_stop_requested)

    def report_quick_test_events(
        self,
        _terminal_id: UUID,
        _credential: str,
        _session_id: UUID,
        payload: QuickTestEventBatchPayload,
    ) -> QuickTestEventReceiptPayload:
        if self.quick_event_error is not None:
            raise self.quick_event_error
        self.quick_event_batches.append(payload)
        return QuickTestEventReceiptPayload(last_sequence=payload.items[-1].sequence)

    def start_attempt(
        self,
        _terminal_id: UUID,
        _credential: str,
        _attempt_id: UUID,
        payload: AttemptStartPayload,
    ) -> AttemptStartResultPayload:
        self.start_calls.append(payload)
        if self.start_error is not None:
            raise self.start_error
        self.execution_events.append(("start", payload.package_id))
        return AttemptStartResultPayload(
            report_id=payload.report_id,
            disposition="accepted",
            execution_status="running",
            attempt_status="running",
            lease_id=uuid4(),
            lease_version=1,
            lease_expires_at=NOW + timedelta(minutes=5),
        )

    def submit_attempt_result(
        self,
        _terminal_id: UUID,
        _credential: str,
        _attempt_id: UUID,
        payload: AttemptResultPayload,
    ) -> AttemptResultReceiptPayload:
        self.result_calls.append(payload)
        self.execution_events.append(("result", payload.package_id))
        return AttemptResultReceiptPayload(
            report_id=payload.report_id,
            disposition="accepted",
            execution_status="succeeded",
            attempt_status="succeeded",
            retry_attempt_id=None,
        )

    def submit_quick_test_result(
        self,
        _terminal_id: UUID,
        _credential: str,
        _script_id: UUID,
        payload: QuickTestResultPayload,
        *,
        idempotency_key: str,
    ) -> QuickTestResultReceiptPayload:
        assert idempotency_key
        self.quick_result_calls.append(payload)
        return QuickTestResultReceiptPayload(
            session_id=payload.session_id,
            session_status="passed" if payload.passed else "failed",
            receipt_id=uuid4(),
            qualification_status="qualified" if payload.passed else "rejected",
            completed_at=NOW,
        )


class FakeDiscovery:
    def __init__(self, serial: str | None) -> None:
        self.serial = serial

    def serial_for_target(self, _target_device_id: UUID) -> str | None:
        return self.serial


class FakeAdb:
    def __init__(self) -> None:
        self.cleanup_calls: list[tuple[str, str | None]] = []

    def require_device(self, serial: str | None = None) -> AdbDevice:
        assert serial == "phone-1"
        return AdbDevice("phone-1", AdbDeviceState.ONLINE, "Galaxy", None, "1")

    def force_stop_and_home(self, serial: str, package_name: str | None) -> None:
        self.cleanup_calls.append((serial, package_name))


class FakeMaaRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def run(
        self,
        _task: CompiledMaaTask,
        *,
        adb_serial: str,
        timeout_seconds: int,
        capture_failure: bool,
        cancel_requested: object = None,
        on_event: Any = None,
    ) -> dict[str, Any]:
        assert isinstance(capture_failure, bool)
        self.calls.append((adb_serial, timeout_seconds))
        if on_event is not None:
            on_event("step_started", 1)
            on_event("step_succeeded", 1)
        return {"success": True, "executed_steps": 1}


def _identity(tmp_path: Path, terminal_id: UUID) -> FileSecretStore:
    store = FileSecretStore(tmp_path / "secrets" / "terminal-credential")
    store.save(SecureIdentity(uuid4(), terminal_id, "credential", 1, 1))
    return store


def _formal_work(
    engine: Engine, target_id: UUID, *, record_video: bool = False, legacy_queue: bool = False
) -> UUID:
    package_id = uuid4()
    attempt_id = uuid4()
    now = NOW
    with LocalUnitOfWork.from_engine(engine) as uow:
        work = uow.work_items.add(
            kind=WorkItemKind.FORMAL_TASK,
            remote_id=package_id,
            content_hash="1" * 64,
            target_device_id=target_id,
            available_at=now,
            payload={},
            now=now,
        )
        uow.packages.add_receiving(
            package_id=package_id,
            work_item_id=work.work_item_id,
            attempt_id=attempt_id,
            execution_id=uuid4(),
            package_hash="1" * 64,
            protocol_version=1,
            package_schema_version=1,
            snapshot_schema_version=1,
            manifest={
                **(
                    {}
                    if legacy_queue
                    else {
                        "queue_order": {
                            "available_at": NOW.isoformat(),
                            "enqueued_at": NOW.isoformat(),
                        }
                    }
                ),
                "manifest": _definition(),
                "timeout_seconds": 60,
                "record_video": record_video,
            },
            now=now,
        )
        uow.packages.mark_ready(package_id, relative_path="packages/test.json", now=now)
        uow.work_items.transition(
            work.work_item_id,
            expected=WorkItemStatus.RECEIVING,
            target=WorkItemStatus.QUEUED,
            now=now,
        )
        uow.offline_permits.save_issued(
            permit_id=uuid4(),
            work_item_id=work.work_item_id,
            attempt_id=attempt_id,
            package_id=package_id,
            package_hash="1" * 64,
            permit_version=1,
            token="test-offline-start-permit",
            issued_at=now,
            expires_at=now + timedelta(hours=1),
        )
        return work.work_item_id


def _quick_test_work(
    engine: Engine, tmp_path: Path, target_id: UUID, *, debug_step_number: int | None = None
) -> UUID:
    session_id = uuid4()
    script_id = uuid4()
    candidate_version_id = uuid4()
    definition_hash = "2" * 64
    manifest = _definition()
    if debug_step_number is not None:
        manifest["debug_step_number"] = debug_step_number
    relative_path = ContentAddressedStore(tmp_path).write_quick_test(
        session_id,
        {"definition": {"manifest": manifest}},
    )
    with LocalUnitOfWork.from_engine(engine) as uow:
        work = uow.work_items.add(
            kind=WorkItemKind.QUICK_TEST,
            remote_id=session_id,
            content_hash=definition_hash,
            target_device_id=target_id,
            available_at=NOW,
            payload={
                "script_id": str(script_id),
                "candidate_version_id": str(candidate_version_id),
                "candidate_manifest_hash": "3" * 64,
                "definition_path": relative_path,
                "expires_at": (NOW + timedelta(minutes=10)).isoformat(),
            },
            now=NOW,
        )
        uow.work_items.transition(
            work.work_item_id,
            expected=WorkItemStatus.RECEIVING,
            target=WorkItemStatus.QUEUED,
            now=NOW,
        )
        return work.work_item_id


def _coordinator(
    engine: Engine,
    tmp_path: Path,
    platform: FakePlatform,
    terminal_id: UUID,
    runner: FakeMaaRunner,
    *,
    serial: str | None = "phone-1",
    adb: FakeAdb | None = None,
    interactive_gate: object | None = None,
) -> ExecutionCoordinator:
    store = ContentAddressedStore(tmp_path)
    return ExecutionCoordinator(
        platform=platform,  # type: ignore[arg-type]
        secret_store=_identity(tmp_path, terminal_id),
        device_discovery=FakeDiscovery(serial),  # type: ignore[arg-type]
        adb=adb or FakeAdb(),  # type: ignore[arg-type]
        loader=ExecutionWorkLoader(
            content_store=store,
            uow_factory=lambda: LocalUnitOfWork.from_engine(engine),
        ),
        engine=MaaExecutionEngine(
            compiler=MaaPlanCompiler(tmp_path),
            runner=runner,
        ),
        uow_factory=lambda: LocalUnitOfWork.from_engine(engine),
        executor_version="0.1.0",
        interactive_gate=interactive_gate,  # type: ignore[arg-type]
        clock=lambda: NOW,
    )


class FakeInteractiveGate:
    def __init__(self, *, reject: bool = False) -> None:
        self.reject = reject
        self.events: list[tuple[str, str]] = []

    def begin_automation(self, serial: str) -> None:
        self.events.append(("begin", serial))
        if self.reject:
            raise InteractiveSessionError(
                "interactive_control_revoke_failed",
                "control connection could not be revoked",
            )

    def end_automation(self, serial: str) -> None:
        self.events.append(("end", serial))


@pytest.mark.parametrize("positions", [[0, 1], [0, 1, 2], [0, 0], [-1, 1]])
def test_strategy_package_preflight_preserves_order_and_specific_errors(
    local_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    positions: list[int],
) -> None:
    import copy
    import sys

    definition = _definition()
    base = definition["definitions"]["main"]
    definitions = {}
    modules = []
    for index, position in enumerate(positions):
        key = f"module-{index}"
        module = copy.deepcopy(base)
        module["script_type"] = (
            "module_start"
            if index == 0
            else "module_end"
            if index == len(positions) - 1
            else "module_process"
        )
        definitions[key] = module
        modules.append({"position": position, "definition_key": key, "wait_after_ms": 0})
    definition = {
        "schema_version": 1,
        "definition_type": "strategy",
        "modules": list(reversed(modules)),
        "definitions": definitions,
    }
    monkeypatch.setattr(sys.modules[__name__], "_definition", lambda: definition)
    _formal_work(local_engine, uuid4())
    runner = FakeMaaRunner()
    result = _coordinator(local_engine, tmp_path, FakePlatform(), uuid4(), runner).run_once()
    valid = positions in ([0, 1], [0, 1, 2])
    assert result.disposition == ("result_queued" if valid else "failed_preflight")
    assert len(runner.calls) == (len(positions) if valid else 0)
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        reports = uow.outbox.claim_batch(
            worker_id="inspect",
            now=NOW,
            lease_duration=timedelta(seconds=1),
            limit=10,
        )
        payload = reports[0].payload
    assert payload["error_code"] == (None if valid else "maa_strategy_module_invalid")
    if valid:
        assert [m["definition_key"] for m in payload["diagnostic"]["modules"]] == [
            m["definition_key"] for m in modules
        ]


def test_formal_execution_persists_result_before_platform_confirmation(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    terminal_id = uuid4()
    target_id = uuid4()
    work_item_id = _formal_work(local_engine, target_id)
    platform = FakePlatform()
    runner = FakeMaaRunner()
    adb = FakeAdb()

    result = _coordinator(local_engine, tmp_path, platform, terminal_id, runner, adb=adb).run_once()

    assert result.disposition == "result_queued"
    assert platform.start_calls
    assert runner.calls == [("phone-1", 60)]
    assert adb.cleanup_calls == [("phone-1", None)]
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        work = uow.work_items.get(work_item_id)
        assert work is not None
        assert work.status is WorkItemStatus.RESULT_PENDING
        reports = uow.outbox.claim_batch(
            worker_id="inspect",
            now=NOW,
            lease_duration=timedelta(seconds=1),
            limit=10,
        )
        assert len(reports) == 1
        assert reports[0].status is OutboxStatus.CLAIMED


def test_automation_gate_wraps_phone_execution_and_is_released(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    terminal_id = uuid4()
    target_id = uuid4()
    _formal_work(local_engine, target_id)
    platform = FakePlatform()
    gate = FakeInteractiveGate()

    result = _coordinator(
        local_engine,
        tmp_path,
        platform,
        terminal_id,
        FakeMaaRunner(),
        interactive_gate=gate,
    ).run_once()

    assert result.disposition == "result_queued"
    assert gate.events == [("begin", "phone-1"), ("end", "phone-1")]


def test_control_revoke_failure_keeps_work_queued_and_does_not_start_attempt(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    terminal_id = uuid4()
    work_item_id = _formal_work(local_engine, uuid4())
    platform = FakePlatform()
    gate = FakeInteractiveGate(reject=True)

    result = _coordinator(
        local_engine,
        tmp_path,
        platform,
        terminal_id,
        FakeMaaRunner(),
        interactive_gate=gate,
    ).run_once()

    assert result.disposition == "interactive_blocked"
    assert result.error_code == "interactive_control_revoke_failed"
    assert gate.events == [("begin", "phone-1"), ("end", "phone-1")]
    assert not platform.start_calls
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        work = uow.work_items.get(work_item_id)
        assert work is not None
        assert work.status is WorkItemStatus.QUEUED


def test_device_unavailable_keeps_formal_work_queued(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    terminal_id = uuid4()
    work_item_id = _formal_work(local_engine, uuid4())
    platform = FakePlatform()

    result = _coordinator(
        local_engine,
        tmp_path,
        platform,
        terminal_id,
        FakeMaaRunner(),
        serial=None,
    ).run_once()

    assert result.disposition == "device_unavailable"
    assert not platform.start_calls
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        work = uow.work_items.get(work_item_id)
        assert work is not None
        assert work.status is WorkItemStatus.QUEUED


def test_confirmed_formal_result_completes_local_work_item(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    terminal_id = uuid4()
    work_item_id = _formal_work(local_engine, uuid4())
    platform = FakePlatform()
    _coordinator(
        local_engine,
        tmp_path,
        platform,
        terminal_id,
        FakeMaaRunner(),
    ).run_once()
    delivery = DeliveryCoordinator(
        platform=platform,  # type: ignore[arg-type]
        secret_store=_identity(tmp_path, terminal_id),
        package_receiver=object(),  # type: ignore[arg-type]
        quick_test_receiver=object(),  # type: ignore[arg-type]
        uow_factory=lambda: LocalUnitOfWork.from_engine(local_engine),
        clock=lambda: NOW,
    )

    assert delivery.flush_outbox() == 1
    assert len(platform.result_calls) == 1
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        work = uow.work_items.get(work_item_id)
        assert work is not None
        assert work.status is WorkItemStatus.COMPLETED


def test_quick_test_stop_before_start_keeps_maa_idle(local_engine: Engine, tmp_path: Path) -> None:
    terminal_id = uuid4()
    work_item_id = _quick_test_work(local_engine, tmp_path, uuid4())
    platform = FakePlatform()
    platform.quick_stop_requested = True
    runner = FakeMaaRunner()
    coordinator = _coordinator(local_engine, tmp_path, platform, terminal_id, runner)

    result = coordinator.run_once()

    assert result.disposition == "result_queued"
    assert result.error_code == "execution_cancelled"
    assert runner.calls == []
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        work = uow.work_items.get(work_item_id)
        assert work is not None and work.status is WorkItemStatus.RESULT_PENDING


def test_offline_start_is_reported_before_its_persisted_result(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    terminal_id = uuid4()
    work_item_id = _formal_work(local_engine, uuid4())
    platform = FakePlatform()
    platform.start_error = PlatformUnavailableError(503, "platform_unavailable", "offline")
    runner = FakeMaaRunner()
    coordinator = _coordinator(
        local_engine,
        tmp_path,
        platform,
        terminal_id,
        runner,
    )

    cycle = coordinator.run_once()

    assert cycle.disposition == "result_queued"
    assert len(platform.start_calls) == 1
    assert runner.calls == [("phone-1", 60)]
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        work = uow.work_items.get(work_item_id)
        assert work is not None
        assert work.status is WorkItemStatus.RESULT_PENDING
        assert work.lease_id is None

    platform.start_error = None
    delivery = DeliveryCoordinator(
        platform=platform,  # type: ignore[arg-type]
        secret_store=_identity(tmp_path, terminal_id),
        package_receiver=object(),  # type: ignore[arg-type]
        quick_test_receiver=object(),  # type: ignore[arg-type]
        uow_factory=lambda: LocalUnitOfWork.from_engine(local_engine),
        clock=lambda: NOW,
    )

    assert delivery.flush_outbox() == 1
    assert len(platform.result_calls) == 0
    assert delivery.flush_outbox() == 1
    assert len(platform.start_calls) == 2
    assert len(platform.result_calls) == 1
    assert platform.result_calls[0].lease_id is not None
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        work = uow.work_items.get(work_item_id)
        assert work is not None
        assert work.status is WorkItemStatus.COMPLETED
        assert work.lease_id == platform.result_calls[0].lease_id


def test_offline_queue_continues_and_replays_in_execution_order(
    local_engine: Engine, tmp_path: Path
) -> None:
    target_id, terminal_id = uuid4(), uuid4()
    work_ids = {_formal_work(local_engine, target_id), _formal_work(local_engine, target_id)}
    platform = FakePlatform()
    platform.start_error = PlatformUnavailableError(503, "platform_unavailable", "offline")
    runner = FakeMaaRunner()
    coordinator = _coordinator(local_engine, tmp_path, platform, terminal_id, runner)
    cycles = [coordinator.run_once(), coordinator.run_once()]
    assert {cycle.work_item_id for cycle in cycles} == work_ids
    assert all(cycle.disposition == "result_queued" for cycle in cycles)
    assert len(runner.calls) == 2
    # A fresh coordinator must not replay durable results after a restart.
    restarted = _coordinator(local_engine, tmp_path, platform, terminal_id, runner)
    assert restarted.run_once().disposition == "idle"
    assert len(runner.calls) == 2
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        packages = [uow.packages.get_by_work_item(cycle.work_item_id) for cycle in cycles]
    assert all(package is not None for package in packages)
    platform.start_error = None
    delivery = DeliveryCoordinator(
        platform=platform,  # type: ignore[arg-type]
        secret_store=_identity(tmp_path, terminal_id),
        package_receiver=object(),  # type: ignore[arg-type]
        quick_test_receiver=object(),  # type: ignore[arg-type]
        uow_factory=lambda: LocalUnitOfWork.from_engine(local_engine),
        clock=lambda: NOW,
    )
    for _ in range(4):
        assert delivery.flush_outbox() == 1
    assert platform.execution_events == [
        (kind, package.package_id)
        for package in packages
        if package is not None
        for kind in ("start", "result")
    ]
    assert delivery.flush_outbox() == 0


def test_legacy_package_cannot_guess_offline_fifo(local_engine: Engine, tmp_path: Path) -> None:
    work_id = _formal_work(local_engine, uuid4(), legacy_queue=True)
    platform = FakePlatform()
    platform.start_error = PlatformUnavailableError(503, "platform_unavailable", "offline")
    runner = FakeMaaRunner()
    coordinator = _coordinator(local_engine, tmp_path, platform, uuid4(), runner)
    result = coordinator.run_once()
    assert result.disposition == "start_deferred"
    assert not runner.calls
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        work = uow.work_items.get(work_id)
        assert work is not None and work.status is WorkItemStatus.QUEUED


def test_quick_test_uses_its_own_result_contract_without_starting_formal_attempt(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    terminal_id = uuid4()
    work_item_id = _quick_test_work(local_engine, tmp_path, uuid4())
    platform = FakePlatform()
    runner = FakeMaaRunner()
    adb = FakeAdb()
    coordinator = _coordinator(
        local_engine,
        tmp_path,
        platform,
        terminal_id,
        runner,
        adb=adb,
    )

    result = coordinator.run_once()

    assert result.disposition == "result_queued"
    assert not platform.start_calls
    assert runner.calls == [("phone-1", 600)]
    assert adb.cleanup_calls == []
    delivery = DeliveryCoordinator(
        platform=platform,  # type: ignore[arg-type]
        secret_store=_identity(tmp_path, terminal_id),
        package_receiver=object(),  # type: ignore[arg-type]
        quick_test_receiver=object(),  # type: ignore[arg-type]
        uow_factory=lambda: LocalUnitOfWork.from_engine(local_engine),
        clock=lambda: NOW,
    )
    assert delivery.flush_outbox() == 1
    assert len(platform.quick_result_calls) == 1
    assert delivery.flush_quick_test_events() == 3
    assert [item.kind for item in platform.quick_event_batches[0].items] == [
        "started",
        "step_started",
        "step_succeeded",
    ]
    assert all(item.created_at.tzinfo is UTC for item in platform.quick_event_batches[0].items)
    assert platform.quick_result_calls[0].session_id
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        work = uow.work_items.get(work_item_id)
        assert work is not None
        assert work.status is WorkItemStatus.COMPLETED


def test_closed_quick_test_events_do_not_block_later_session(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    terminal_id = uuid4()
    first_id = _quick_test_work(local_engine, tmp_path, uuid4())
    second_id = _quick_test_work(local_engine, tmp_path, uuid4())
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        first = uow.work_items.get(first_id)
        second = uow.work_items.get(second_id)
        assert first is not None and second is not None
        uow.quick_test_events.append(first, kind="started", step_number=None, now=NOW)
        uow.quick_test_events.append(
            second, kind="started", step_number=None, now=NOW + timedelta(seconds=1)
        )
    platform = FakePlatform()
    delivery = DeliveryCoordinator(
        platform=platform,  # type: ignore[arg-type]
        secret_store=_identity(tmp_path, terminal_id),
        package_receiver=object(),  # type: ignore[arg-type]
        quick_test_receiver=object(),  # type: ignore[arg-type]
        uow_factory=lambda: LocalUnitOfWork.from_engine(local_engine),
        clock=lambda: NOW,
    )
    platform.quick_event_error = PlatformUnavailableError(503, "platform_unavailable", "offline")
    with pytest.raises(PlatformUnavailableError):
        delivery.flush_quick_test_events()
    platform.quick_event_error = PlatformError(409, "quick_test_event_window_closed", "expired")
    assert delivery.flush_quick_test_events() == 0
    platform.quick_event_error = None
    assert delivery.flush_quick_test_events() == 1
    assert platform.quick_event_batches[0].items[0].sequence == 1
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        assert uow.quick_test_events.pending_batch() == ()


def test_expired_quick_test_event_gc_removes_complete_session(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    first_id = _quick_test_work(local_engine, tmp_path, uuid4())
    second_id = _quick_test_work(local_engine, tmp_path, uuid4())
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        first = uow.work_items.get(first_id)
        second = uow.work_items.get(second_id)
        assert first is not None and second is not None
        uow.quick_test_events.append(
            first, kind="started", step_number=None, now=NOW - timedelta(days=8)
        )
        uow.quick_test_events.append(first, kind="step_started", step_number=1, now=NOW)
        uow.quick_test_events.append(second, kind="started", step_number=None, now=NOW)
        assert uow.quick_test_events.delete_expired(before=NOW - timedelta(days=7)) == 2
        remaining = uow.quick_test_events.pending_batch()
        assert len(remaining) == 1
        assert remaining[0].session_id == second.remote_id


def test_quick_test_failure_records_bounded_error_event(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    _quick_test_work(local_engine, tmp_path, uuid4())

    class FailingRunner(FakeMaaRunner):
        def run(self, *args: object, **kwargs: object) -> dict[str, Any]:
            raise MaaRuntimeError("execution_timeout", "timed out")

    result = _coordinator(
        local_engine, tmp_path, FakePlatform(), uuid4(), FailingRunner()
    ).run_once()
    assert result.disposition == "result_queued"
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        events = uow.quick_test_events.pending_batch()
        assert [(item.kind, item.code) for item in events] == [
            ("started", None),
            ("log", "execution_timeout"),
        ]


def test_single_step_events_keep_original_editor_step_number(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    _quick_test_work(local_engine, tmp_path, uuid4(), debug_step_number=5)
    result = _coordinator(
        local_engine, tmp_path, FakePlatform(), uuid4(), FakeMaaRunner()
    ).run_once()
    assert result.disposition == "result_queued"
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        events = uow.quick_test_events.pending_batch()
        assert [item.step_number for item in events] == [None, 5, 5]


def test_single_step_rule_events_remain_diagnostic_logs_with_original_rule_identity(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    class RuleRunner(FakeMaaRunner):
        def run(self, task: CompiledMaaTask, **options: Any) -> dict[str, Any]:
            emit = options["on_event"]
            emit("step_started", 1)
            emit("rule_started", 1)
            emit("rule_succeeded", 1)
            emit("step_succeeded", 1)
            return {"success": True}

    _quick_test_work(local_engine, tmp_path, uuid4(), debug_step_number=5)
    result = _coordinator(local_engine, tmp_path, FakePlatform(), uuid4(), RuleRunner()).run_once()
    assert result.disposition == "result_queued"
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        events = uow.quick_test_events.pending_batch()
        assert [item.kind for item in events] == [
            "started",
            "step_started",
            "log",
            "log",
            "step_succeeded",
        ]
        assert [item.step_number for item in events] == [None, 5, None, None, 5]
        assert events[2].code.startswith("maa_rule_started:") and events[2].code.endswith(":1")
        assert events[3].code.startswith("maa_rule_succeeded:") and events[3].code.endswith(":1")
