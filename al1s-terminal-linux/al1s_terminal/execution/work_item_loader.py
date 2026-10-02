from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import UUID

from al1s_terminal.execution.content_store import ContentAddressedStore
from al1s_terminal.execution.maa_definition import MaaDefinitionLoader, MaaExecutionPlan
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.types import PackageManifestRecord, WorkItemKind, WorkItemRecord


class ExecutionWorkError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class LoadedExecutionWork:
    work_item: WorkItemRecord
    plan: MaaExecutionPlan | None
    timeout_seconds: int
    package: PackageManifestRecord | None
    script_id: UUID | None
    expires_at: datetime | None
    record_video: bool
    capability_requirements: dict[str, object] = field(default_factory=dict)
    debug_step_number: int | None = None
    local_manifest: dict[str, object] | None = None
    local_resources: dict[str, Path] = field(default_factory=dict)


class ExecutionWorkLoader:
    def __init__(
        self,
        *,
        content_store: ContentAddressedStore,
        uow_factory: Callable[[], LocalUnitOfWork],
    ) -> None:
        self._content_store = content_store
        self._uow_factory = uow_factory
        self._definitions = MaaDefinitionLoader()

    def load(self, work_item: WorkItemRecord) -> LoadedExecutionWork:
        resources = self._resource_paths(work_item.work_item_id)
        if work_item.kind is WorkItemKind.FORMAL_TASK:
            return self._formal(work_item, resources)
        return self._quick_test(work_item, resources)

    def failure_context(self, work_item: WorkItemRecord) -> LoadedExecutionWork:
        if work_item.kind is WorkItemKind.FORMAL_TASK:
            package, timeout = self._formal_metadata(work_item)
            return LoadedExecutionWork(
                work_item,
                None,
                timeout,
                package,
                None,
                None,
                False,
                _requirements(package.manifest),
            )
        script_id = _uuid(work_item.payload.get("script_id"), "quick test script id")
        expires_at = _datetime(work_item.payload.get("expires_at"), "quick test expiry")
        return LoadedExecutionWork(
            work_item,
            None,
            min(14_400, max(1, int((expires_at - work_item.created_at).total_seconds()))),
            None,
            script_id,
            expires_at,
            False,
        )

    def _formal(
        self,
        work_item: WorkItemRecord,
        resources: dict[str, Path],
    ) -> LoadedExecutionWork:
        package, timeout = self._formal_metadata(work_item)
        manifest = package.manifest.get("manifest")
        if not isinstance(manifest, dict):
            raise ExecutionWorkError(
                "maa_definition_missing", "task package has no Maa execution definition"
            )
        if manifest.get('executor') == 'lineup-recognition-v1':
            return LoadedExecutionWork(
                work_item, None, timeout, package, None, None, False,
                _requirements(package.manifest), local_manifest=manifest, local_resources=resources,
            )
        return LoadedExecutionWork(
            work_item=work_item,
            plan=self._definitions.load(manifest, resources=resources),
            timeout_seconds=timeout,
            package=package,
            script_id=None,
            expires_at=None,
            record_video=self._record_video(package),
            capability_requirements=_requirements(package.manifest),
        )

    def _formal_metadata(self, work_item: WorkItemRecord) -> tuple[PackageManifestRecord, int]:
        with self._uow_factory() as uow:
            package = uow.packages.get_by_work_item(work_item.work_item_id)
        if package is None:
            raise ExecutionWorkError("package_manifest_missing", "formal work has no package")
        timeout = package.manifest.get("timeout_seconds")
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 14_400:
            raise ExecutionWorkError("execution_timeout_invalid", "task timeout is invalid")
        return package, timeout

    @staticmethod
    def _record_video(package: PackageManifestRecord) -> bool:
        value = package.manifest.get("record_video")
        if not isinstance(value, bool):
            raise ExecutionWorkError("record_video_invalid", "task recording selection is invalid")
        return value

    def _quick_test(
        self,
        work_item: WorkItemRecord,
        resources: dict[str, Path],
    ) -> LoadedExecutionWork:
        relative_path = work_item.payload.get("definition_path")
        if not isinstance(relative_path, str):
            raise ExecutionWorkError(
                "quick_test_definition_missing", "quick test definition path is missing"
            )
        claimed = self._content_store.read_json(relative_path)
        definition = claimed.get("definition")
        if not isinstance(definition, dict) or not isinstance(definition.get("manifest"), dict):
            raise ExecutionWorkError(
                "quick_test_definition_invalid", "quick test definition is invalid"
            )
        script_id = _uuid(work_item.payload.get("script_id"), "quick test script id")
        expires_at = _datetime(work_item.payload.get("expires_at"), "quick test expiry")
        debug_step_number = definition["manifest"].get("debug_step_number")
        if debug_step_number is not None and (
            type(debug_step_number) is not int or not 1 <= debug_step_number <= 1000
        ):
            raise ExecutionWorkError(
                "quick_test_definition_invalid", "debug step number is invalid"
            )
        return LoadedExecutionWork(
            work_item=work_item,
            plan=self._definitions.load(definition["manifest"], resources=resources),
            timeout_seconds=min(
                14_400, max(1, int((expires_at - work_item.created_at).total_seconds()))
            ),
            package=None,
            script_id=script_id,
            expires_at=expires_at,
            record_video=False,
            capability_requirements=_requirements(definition),
            debug_step_number=debug_step_number,
        )

    def _resource_paths(self, work_item_id: UUID) -> dict[str, Path]:
        with self._uow_factory() as uow:
            resources = uow.resources.list_for_work_item(work_item_id)
        return {
            item.resource_key: self._content_store.path_for_relative(item.relative_path)
            for item in resources
        }


def _uuid(value: object, name: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ExecutionWorkError("quick_test_definition_invalid", f"{name} is invalid") from exc


def _datetime(value: object, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ExecutionWorkError("quick_test_definition_invalid", f"{name} is invalid") from exc
    if parsed.tzinfo is None:
        raise ExecutionWorkError("quick_test_definition_invalid", f"{name} has no timezone")
    return parsed


def _requirements(container: dict[str, object]) -> dict[str, object]:
    value = container.get("capability_requirements")
    if value is None:
        return {}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ExecutionWorkError(
            "capability_requirements_invalid", "capability requirements are invalid"
        )
    return dict(value)
