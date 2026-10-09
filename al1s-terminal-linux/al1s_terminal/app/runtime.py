from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from threading import Event, Lock
from typing import cast

import structlog
from sqlalchemy.exc import SQLAlchemyError

from al1s_terminal.app.background import SingleFlight
from al1s_terminal.app.cancellation import CancellationCoordinator
from al1s_terminal.app.config import TerminalSettings
from al1s_terminal.app.delivery import DeliveryCoordinator
from al1s_terminal.app.device_discovery import TargetDeviceDiscoveryService
from al1s_terminal.app.device_path_coordinator import DevicePathCoordinator
from al1s_terminal.app.editor_coordinator import EditorCoordinator
from al1s_terminal.app.execution_coordinator import ExecutionCoordinator, ExecutionCycleResult
from al1s_terminal.app.lifecycle import TerminalLifecycleService
from al1s_terminal.app.local_gc import LocalGarbageCollector, LocalGcUnitOfWork
from al1s_terminal.app.secrets import FileSecretStore
from al1s_terminal.execution.artifact_store import ExecutionArtifactCollector, LocalArtifactStore
from al1s_terminal.execution.artifact_uploader import ArtifactUploadCoordinator, ArtifactUploader
from al1s_terminal.execution.content_store import ContentAddressedStore
from al1s_terminal.execution.device_authority import PathRuntimeOwner
from al1s_terminal.execution.maa_plan_compiler import MaaPlanCompiler
from al1s_terminal.execution.maa_runtime import MaaExecutionEngine
from al1s_terminal.execution.maa_smoke import MaaRuntimeReadinessProbe
from al1s_terminal.execution.maa_task_runner import MaaFrameworkTaskRunner
from al1s_terminal.execution.package_receiver import PackageReceiver
from al1s_terminal.execution.quick_test_receiver import QuickTestReceiver
from al1s_terminal.execution.resource_receiver import AuthorizedResourceReceiver
from al1s_terminal.execution.work_item_loader import ExecutionWorkLoader
from al1s_terminal.interactive.relay import AdbScrcpyRelay
from al1s_terminal.interactive.scrcpy_process import ScrcpyProcessFactory
from al1s_terminal.interactive.sessions import InteractiveAutomationGate, InteractiveSessionManager
from al1s_terminal.interactive.tls import server_context
from al1s_terminal.lineup.executor import LineupExecutor
from al1s_terminal.persistence.base import create_local_engine
from al1s_terminal.persistence.migrations import upgrade_database
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.providers.adb import AdbProbeResult, AdbProvider
from al1s_terminal.providers.maa import MaaProvider
from al1s_terminal.providers.recorder import AndroidScreenRecorder
from al1s_terminal.providers.resource_guard import RuntimeResourceGuard
from al1s_terminal.providers.rknn_smoke import RknnRuntimeReadinessProbe
from al1s_terminal.providers.system import (
    SystemCapability,
    include_adb_capability,
    include_maa_capability,
    include_rknn_capability,
    include_scrcpy_capability,
    probe_system,
)
from al1s_terminal.transport.editor import EditorPlatformClient
from al1s_terminal.transport.mqtt_hints import MqttHintConnectionError, MqttHintListener
from al1s_terminal.transport.platform import (
    HttpPlatformClient,
    PlatformCredentialRejectedError,
    PlatformError,
)

logger = structlog.get_logger(__name__)


class TerminalRuntime:
    def __init__(self, settings: TerminalSettings) -> None:
        self._settings = settings
        self._path_owner: PathRuntimeOwner | None = None
        self._shutdown_requested = Event()
        self._lifecycle_lock = Lock()
        self._reconciliation_requested = Event()
        self._execution_pool = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="al1s-execution",
        )
        self._execution_future: Future[ExecutionCycleResult] | None = None
        self._delivery_worker = SingleFlight("al1s-delivery")
        self._media_worker = SingleFlight("al1s-media")
        self._io_workers = {
            name: SingleFlight(f"al1s-{name}")
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
                "paths",
            )
        }
        upgrade_database(settings.database_url)
        self._engine = create_local_engine(settings.database_url)
        self._platform = HttpPlatformClient(
            settings.platform_url,
            timeout_seconds=settings.http_timeout_seconds,
            ca_file=settings.tls_ca_file,
        )

        def uow_factory() -> LocalUnitOfWork:
            return LocalUnitOfWork.from_engine(self._engine)

        self._uow_factory = uow_factory

        secret_store = FileSecretStore(settings.secret_path)
        self._secret_store = secret_store
        self._mqtt_hints = MqttHintListener(
            self._reconciliation_requested.set,
            ca_file=settings.tls_ca_file,
        )
        self._lifecycle = TerminalLifecycleService(
            settings=settings,
            platform=self._platform,
            secret_store=secret_store,
            uow_factory=uow_factory,
        )
        content_store = ContentAddressedStore(settings.data_dir)
        artifact_store = LocalArtifactStore(settings.data_dir)
        self._gc = LocalGarbageCollector(
            uow_factory=cast(Callable[[], LocalGcUnitOfWork], uow_factory),
            artifact_store=artifact_store,
            content_store=content_store,
            retention=timedelta(seconds=settings.gc_retention_seconds),
            batch_size=settings.gc_batch_size,
        )
        resource_receiver = AuthorizedResourceReceiver(
            platform=self._platform,
            content_store=content_store,
            uow_factory=uow_factory,
        )
        package_receiver = PackageReceiver(
            platform=self._platform,
            secret_store=secret_store,
            content_store=content_store,
            uow_factory=uow_factory,
            resource_receiver=resource_receiver,
        )
        quick_test_receiver = QuickTestReceiver(
            secret_store=secret_store,
            content_store=content_store,
            resource_receiver=resource_receiver,
            uow_factory=uow_factory,
        )
        self._adb = AdbProvider(adb_path=settings.adb_path)
        self._device_discovery = TargetDeviceDiscoveryService(
            adb=self._adb,
            platform=self._platform,
            secret_store=secret_store,
            uow_factory=uow_factory,
        )
        self._interactive_relay: AdbScrcpyRelay | None = None
        self._interactive_relay_available = False
        interactive_gate: InteractiveAutomationGate
        if settings.scrcpy_public_url is None:
            interactive_gate = InteractiveSessionManager(
                idle_timeout_seconds=settings.scrcpy_idle_timeout_seconds,
            )
        else:
            self._interactive_relay = AdbScrcpyRelay(
                host=settings.scrcpy_relay_host,
                port=settings.scrcpy_relay_port,
                public_url=settings.scrcpy_public_url,
                ocr_model_dir=settings.maa_ocr_model_dir,
                adb=self._adb,
                tls_context=server_context(
                    settings.scrcpy_tls_cert_file, settings.scrcpy_tls_key_file
                ),
                process_factory=(
                    ScrcpyProcessFactory(
                        self._adb.binary_path,
                        settings.scrcpy_server_path,
                        settings.scrcpy_server_sha256,
                    )
                    if settings.scrcpy_server_path is not None
                    and settings.scrcpy_server_sha256 is not None
                    else None
                ),
                idle_timeout_seconds=settings.scrcpy_idle_timeout_seconds,
            )
            self._interactive_relay_available = (
                self._interactive_relay.provider_available() and self._interactive_relay.start()
            )
            interactive_gate = self._interactive_relay
        self._maa = MaaProvider(
            data_dir=settings.data_dir,
            ocr_model_dir=settings.maa_ocr_model_dir,
        )
        self._delivery = DeliveryCoordinator(
            platform=self._platform,
            secret_store=secret_store,
            package_receiver=package_receiver,
            quick_test_receiver=quick_test_receiver,
            cancellation_coordinator=CancellationCoordinator(uow_factory=uow_factory),
            uow_factory=uow_factory,
            outbox_batch_size=settings.outbox_batch_size,
        )
        self._artifact_upload = ArtifactUploadCoordinator(
            uploader=ArtifactUploader(
                platform=self._platform,
                secret_store=secret_store,
                data_dir=settings.data_dir,
            ),
            store=artifact_store,
            secret_store=secret_store,
            uow_factory=uow_factory,
            batch_size=settings.artifact_batch_size,
        )
        self._maa_runner = MaaFrameworkTaskRunner(
            data_dir=settings.data_dir,
            adb_path=self._adb.binary_path,
            ocr_model_dir=settings.maa_ocr_model_dir,
            yolo_provider_spec=settings.maa_yolo_provider,
            adb_screencap_methods=settings.maa_adb_screencap_methods,
            adb_input_methods=settings.maa_adb_input_methods,
            screenshot_mode=settings.maa_screenshot_mode,
            screenshot_short_side=settings.maa_screenshot_short_side,
            adb_command_timeout_seconds=settings.maa_adb_command_timeout_seconds,
        )
        self._maa_readiness = MaaRuntimeReadinessProbe(
            runner=self._maa_runner,
            data_dir=settings.data_dir,
        )
        self._rknn_readiness = RknnRuntimeReadinessProbe()
        self._lineup_executor = LineupExecutor()
        self._execution = ExecutionCoordinator(
            platform=self._platform,
            secret_store=secret_store,
            device_discovery=self._device_discovery,
            adb=self._adb,
            loader=ExecutionWorkLoader(
                content_store=content_store,
                uow_factory=uow_factory,
            ),
            engine=MaaExecutionEngine(
                compiler=MaaPlanCompiler(settings.data_dir),
                runner=self._maa_runner,
            ),
            uow_factory=uow_factory,
            executor_version=settings.agent_version,
            lineup_executor=self._lineup_executor,
            artifact_collector=ExecutionArtifactCollector(
                store=artifact_store,
                uow_factory=uow_factory,
            ),
            screen_recorder=AndroidScreenRecorder(
                adb_path=self._adb.binary_path,
                data_dir=settings.data_dir,
            ),
            stop_requested=self._shutdown_requested.is_set,
            resource_guard=RuntimeResourceGuard(
                data_dir=settings.data_dir,
                minimum_available_memory_bytes=settings.minimum_available_memory_bytes,
                minimum_available_storage_bytes=settings.minimum_available_storage_bytes,
                recording_storage_reserve_bytes=settings.recording_storage_reserve_bytes,
            ),
            interactive_gate=interactive_gate,
        )

    def bootstrap(self) -> dict[str, object]:
        installation = self._lifecycle.register_if_needed()
        installation = self._lifecycle.heartbeat()
        adb, _observations = self._device_discovery.synchronize()
        installation = self._lifecycle.publish_capability(self._probe_capability(adb))
        if self._interactive_relay is not None:
            self._interactive_relay.confirm_platform(self._settings.heartbeat_interval_seconds * 3)
        with suppress(PlatformError, MqttHintConnectionError):
            self._refresh_mqtt_session()
        return {
            "installation_id": str(installation.installation_id),
            "terminal_id": str(installation.terminal_id),
            "registration_status": installation.registration_status.value,
            "agent_version": installation.agent_version,
            "capability_revision": installation.capability_revision,
        }

    def run(self, *, stop_requested: Callable[[], bool] | None = None) -> None:
        owner = PathRuntimeOwner(self._settings.data_dir / "device-authority")
        self._path_owner = owner
        should_stop = stop_requested or (lambda: False)
        editor = EditorCoordinator(
            EditorPlatformClient(self._platform._request),
            self._secret_store,
            self._device_discovery,
            self._adb,
            self._interactive_relay,
        )
        self._path_native_ready = False
        paths = DevicePathCoordinator(self._platform._request, self._secret_store, self._adb,
            self._uow_factory, owner, editor, lambda: self._path_native_ready)
        if self._interactive_relay is not None:
            self._interactive_relay.set_input_authority(paths.input.allowed)
        self._bootstrap_for_run()
        now = time.monotonic()
        next_heartbeat = now + self._settings.heartbeat_interval_seconds
        next_reconciliation = now
        next_capability = now + 300
        next_presence = now + 10
        next_quick_test_control = now + 2
        next_quick_test_events = now + 2
        next_mqtt_refresh = now + 60
        next_gc = now + self._settings.gc_interval_seconds
        next_execution = now
        next_editor = now
        next_paths = now
        try:
            while not should_stop():
                now = time.monotonic()
                if now >= next_paths:
                    try:
                        self._io_workers["paths"].poll(paths.cycle)
                    except Exception as exc:
                        logger.warning("device_path_cycle_failed", error_type=type(exc).__name__)
                    next_paths = now + 15
                if self._reconciliation_requested.is_set():
                    self._reconciliation_requested.clear()
                    next_reconciliation = now
                if now >= next_reconciliation:
                    try:
                        self._io_workers["cancel"].poll(self._delivery.poll_cancellations)
                        self._delivery_worker.poll(self._delivery.reconcile)
                        self._media_worker.poll(self._artifact_upload.flush)
                    except PlatformCredentialRejectedError as exc:
                        self._revoke_manual_control()
                        self._lifecycle.handle_credential_rejection(exc)
                    except PlatformError as exc:
                        self._handle_platform_error(exc)
                    except Exception as exc:
                        logger.error("transfer_cycle_failed", error_type=type(exc).__name__)
                    next_reconciliation = now + self._settings.reconciliation_interval_seconds
                if now >= next_editor:
                    try:
                        self._io_workers["editor"].poll(lambda: self._editor_cycle(editor))
                    except PlatformCredentialRejectedError as exc:
                        self._revoke_manual_control()
                        self._lifecycle.handle_credential_rejection(exc)
                    except PlatformError as exc:
                        self._handle_platform_error(exc)
                    except Exception as exc:
                        self._revoke_manual_control()
                        logger.error("editor_cycle_failed", error_type=type(exc).__name__)
                    next_editor = time.monotonic() + 1
                if now >= next_heartbeat:
                    try:
                        self._io_workers["heartbeat"].poll(self._heartbeat_cycle)
                    except PlatformCredentialRejectedError as exc:
                        if self._interactive_relay is not None:
                            self._interactive_relay.disconnect_platform()
                        self._lifecycle.handle_credential_rejection(exc)
                    except PlatformError as exc:
                        self._handle_platform_error(exc)
                    except Exception as exc:
                        self._revoke_manual_control()
                        logger.error("heartbeat_cycle_failed", error_type=type(exc).__name__)
                    next_heartbeat = now + self._settings.heartbeat_interval_seconds
                if now >= next_capability:
                    try:
                        self._io_workers["capability"].poll(self._capability_cycle)
                    except PlatformCredentialRejectedError as exc:
                        self._revoke_manual_control()
                        self._lifecycle.handle_credential_rejection(exc)
                    except PlatformError as exc:
                        self._handle_platform_error(exc)
                    except Exception as exc:
                        logger.error("capability_cycle_failed", error_type=type(exc).__name__)
                    next_capability = now + 300

                if now >= next_presence:
                    try:
                        self._io_workers["presence"].poll(self._device_discovery.report_presence)
                    except PlatformCredentialRejectedError as exc:
                        self._lifecycle.handle_credential_rejection(exc)
                    except PlatformError:
                        pass
                    except Exception as exc:
                        logger.error("adb_presence_cycle_failed", error_type=type(exc).__name__)
                    next_presence = time.monotonic() + 10

                if now >= next_quick_test_control:
                    try:
                        self._io_workers["quick-control"].poll(self._delivery.poll_quick_test_stop)
                    except PlatformCredentialRejectedError as exc:
                        self._lifecycle.handle_credential_rejection(exc)
                    except PlatformError:
                        pass
                    except Exception as exc:
                        logger.error("quick_test_control_failed", error_type=type(exc).__name__)
                    next_quick_test_control = time.monotonic() + 2

                if now >= next_quick_test_events:
                    try:
                        self._io_workers["quick-events"].poll(
                            self._delivery.flush_quick_test_events
                        )
                    except PlatformCredentialRejectedError as exc:
                        self._lifecycle.handle_credential_rejection(exc)
                    except PlatformError:
                        pass
                    except Exception as exc:
                        logger.error("quick_test_event_cycle_failed", error_type=type(exc).__name__)
                    next_quick_test_events = time.monotonic() + 2
                if now >= next_mqtt_refresh:
                    if self._mqtt_hints.needs_refresh():
                        try:
                            self._io_workers["mqtt"].poll(self._refresh_mqtt_session)
                        except PlatformCredentialRejectedError as exc:
                            self._revoke_manual_control()
                            self._lifecycle.handle_credential_rejection(exc)
                        except PlatformError as exc:
                            self._handle_platform_error(exc)
                        except MqttHintConnectionError:
                            pass
                    next_mqtt_refresh = now + 60
                if now >= next_gc:
                    try:
                        self._io_workers["gc"].poll(self._gc_cycle)
                    except (OSError, SQLAlchemyError) as exc:
                        logger.warning("local_gc_cycle_failed", error=str(exc))
                    next_gc = now + self._settings.gc_interval_seconds
                if now >= next_execution:
                    next_execution = time.monotonic() + self._poll_execution()
                _interruptible_wait(
                    min(
                        next_heartbeat,
                        next_reconciliation,
                        next_capability,
                        next_presence,
                        next_quick_test_control,
                        next_quick_test_events,
                        next_mqtt_refresh,
                        next_gc,
                        next_execution,
                        next_editor,
                        next_paths,
                    )
                    - time.monotonic(),
                    should_stop,
                    self._reconciliation_requested,
                )
        finally:
            self._shutdown_requested.set()
            # The instance lock stays owned until this runtime's workers have stopped.
            self._path_owner = owner

    def _heartbeat_cycle(self) -> None:
        with self._lifecycle_lock:
            self._lifecycle.heartbeat()
        if self._interactive_relay is not None:
            self._interactive_relay.confirm_platform(self._settings.heartbeat_interval_seconds * 3)

    def _editor_cycle(self, editor: EditorCoordinator) -> None:
        started = time.monotonic()
        relay = self._interactive_relay
        epoch = relay.confirmation_epoch() if relay is not None else None
        editor.reconcile()
        if relay is not None:
            relay.confirm_editor(10.0 - (time.monotonic() - started), expected_epoch=epoch)

    def _handle_platform_error(self, error: PlatformError) -> None:
        if self._interactive_relay is None:
            return
        if error.status_code in {0, 408, 429, 500, 502, 503, 504}:
            self._interactive_relay.platform_transient_failure()
        else:
            self._revoke_manual_control()

    def _capability_cycle(self) -> None:
        adb, _observations = self._device_discovery.synchronize()
        capability = self._probe_capability(adb)
        with self._lifecycle_lock:
            self._lifecycle.publish_capability(capability)

    def _gc_cycle(self) -> None:
        with self._uow_factory() as uow:
            uow.quick_test_events.delete_expired(
                before=datetime.now(UTC) - timedelta(days=7),
                limit=50,
            )
        self._gc.collect_once()

    def _bootstrap_for_run(self) -> None:
        try:
            self.bootstrap()
        except PlatformError as exc:
            installation = self._lifecycle.require_registered_identity()
            logger.warning(
                "terminal_bootstrap_deferred_offline",
                terminal_id=str(installation.terminal_id),
                error_code=exc.code,
            )

    def _poll_execution(self) -> float:
        if self._execution_future is None:
            self._execution_future = self._execution_pool.submit(self._execution.run_once)
            return 0.25
        if not self._execution_future.done():
            return 0.25
        future = self._execution_future
        self._execution_future = None
        try:
            cycle = future.result()
        except PlatformCredentialRejectedError as exc:
            self._revoke_manual_control()
            self._lifecycle.handle_credential_rejection(exc)
            return 1.0
        if cycle.disposition == "result_queued":
            # Preserve FIFO: only the delivery worker sends durable reports.
            # Large media never delays execution polling or control-plane work.
            self._reconciliation_requested.set()
        if cycle.disposition in {"resource_blocked", "device_unavailable"}:
            return 5.0
        return 0.25

    def _revoke_manual_control(self) -> None:
        if self._interactive_relay is not None:
            self._interactive_relay.disconnect_platform()

    def close(self) -> None:
        self._shutdown_requested.set()
        self._execution_pool.shutdown(wait=True, cancel_futures=True)
        self._lineup_executor.close()
        self._delivery_worker.close()
        self._media_worker.close()
        for worker in self._io_workers.values():
            worker.close()
        self._mqtt_hints.close()
        if self._interactive_relay is not None:
            self._interactive_relay.stop()
        self._platform.close()
        self._engine.dispose()
        if self._path_owner is not None:
            self._path_owner.close()
            self._path_owner = None

    def _probe_capability(self, adb: AdbProbeResult) -> SystemCapability:
        capability: SystemCapability = include_adb_capability(
            probe_system(self._settings.data_dir), adb
        )
        maa = self._maa.probe()
        readiness = self._maa_readiness.probe(maa=maa, adb=adb)
        self._path_native_ready = readiness.ready
        capability = include_maa_capability(capability, maa, readiness=readiness)
        capability = include_scrcpy_capability(
            capability,
            relay_available=self._interactive_relay_available,
            adb_available=bool(adb.online_devices),
        )
        capability = include_rknn_capability(capability, self._rknn_readiness.probe())
        from dataclasses import replace
        if LineupExecutor().available():
            capability = replace(capability, provider_keys=tuple(sorted(
                {*capability.provider_keys, 'lineup-recognition-v1'}
            )))
        return capability

    def _refresh_mqtt_session(self) -> None:
        identity = self._secret_store.load()
        if identity is None:
            return
        session = self._platform.issue_mqtt_session(
            identity.terminal_id,
            identity.credential,
        )
        self._mqtt_hints.apply(session)


def _interruptible_wait(
    seconds: float,
    stop_requested: Callable[[], bool],
    wake_requested: Event,
) -> None:
    deadline = time.monotonic() + seconds
    while not stop_requested() and time.monotonic() < deadline:
        if wake_requested.wait(min(0.25, max(0.0, deadline - time.monotonic()))):
            return
