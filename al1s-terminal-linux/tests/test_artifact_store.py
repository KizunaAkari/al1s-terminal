from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, select

from al1s_terminal.execution.artifact_store import (
    ExecutionArtifactCollector,
    LocalArtifactStore,
    _capture_file_name,
)
from al1s_terminal.execution.maa_definition import MaaExecutableModule, MaaExecutionPlan
from al1s_terminal.execution.maa_runtime import MaaExecutionOutcome
from al1s_terminal.execution.work_item_loader import LoadedExecutionWork
from al1s_terminal.persistence.models import LocalArtifactRow
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.types import (
    PackageManifestRecord,
    PackageManifestStatus,
    WorkItemKind,
)

NOW = datetime(2026, 8, 31, 22, 0, tzinfo=UTC)


def test_nested_module_capture_keeps_script_name_and_step(local_engine: Engine) -> None:
    loaded = _loaded_formal(local_engine, script_name="目标脚本")
    diagnostic = {
        "modules": [
            {
                "module_index": 1,
                "result": {
                    "failed_step": {"number": 54},
                    "failure_screenshot": {"mime": "image/png"},
                },
            }
        ]
    }
    assert (
        _capture_file_name(
            loaded,
            diagnostic,
            ("modules", "0", "result", "failure_screenshot"),
            1,
            Path("source.png"),
        )
        == "目标脚本-第54步.png"
    )


def _uow_factory(engine: Engine) -> Callable[[], LocalUnitOfWork]:
    return lambda: LocalUnitOfWork.from_engine(engine)


def _loaded_formal(engine: Engine, *, script_name: str = "失败脚本") -> LoadedExecutionWork:
    attempt_id = uuid4()
    with LocalUnitOfWork.from_engine(engine) as uow:
        work = uow.work_items.add(
            kind=WorkItemKind.FORMAL_TASK,
            remote_id=uuid4(),
            content_hash="a" * 64,
            target_device_id=uuid4(),
            available_at=NOW,
            payload={},
            now=NOW,
        )
    package = PackageManifestRecord(
        package_id=uuid4(),
        work_item_id=work.work_item_id,
        attempt_id=attempt_id,
        execution_id=uuid4(),
        package_hash="b" * 64,
        protocol_version=1,
        package_schema_version=1,
        snapshot_schema_version=1,
        relative_path="packages/package.json",
        status=PackageManifestStatus.READY,
        manifest={},
        created_at=NOW,
        ready_at=NOW,
        row_version=1,
    )
    module = MaaExecutableModule(
        definition_key="main",
        script_version_id=str(uuid4()),
        script_name=script_name,
        script_type="standard",
        target={},
        steps=(),
        independent_rules=(),
        cleanup_on_finish=False,
        wait_after_ms=0,
    )
    plan = MaaExecutionPlan("script", (module,), {"main": module}, {})
    return LoadedExecutionWork(work, plan, 30, package, None, None, True)


def test_formal_capture_is_staged_and_report_contains_only_artifact_reference(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    source = tmp_path / "runtime" / "evidence" / "source.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png-evidence")
    loaded = _loaded_formal(local_engine)
    collector = ExecutionArtifactCollector(
        store=LocalArtifactStore(tmp_path),
        uow_factory=_uow_factory(local_engine),
        clock=lambda: NOW,
    )

    collected = collector.collect(
        loaded,
        MaaExecutionOutcome(
            False,
            "image_not_matched",
            {
                "module_index": 1,
                "failed_step": {"number": 2},
                "failure_screenshot": {
                    "path": str(source),
                    "mime": "image/png",
                    "data_base64": "not-persisted",
                },
            },
        ),
    )

    capture = collected.diagnostic["failure_screenshot"]
    assert set(capture) == {"artifact_id", "artifact_kind", "file_name"}
    assert capture["file_name"] == "失败脚本-第02步.png"
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        artifact = uow.artifacts.get(UUID(capture["artifact_id"]))
    assert artifact is not None
    assert artifact.file_name == capture["file_name"]
    assert (tmp_path / artifact.relative_path).read_bytes() == b"png-evidence"
    assert not source.exists()


def test_failed_screenshot_staging_reports_explicit_no_image_reason(
    local_engine: Engine, tmp_path: Path
) -> None:
    store = Mock(spec=LocalArtifactStore)
    store.stage.side_effect = OSError("disk unavailable")
    collector = ExecutionArtifactCollector(
        store=store, uow_factory=_uow_factory(local_engine), clock=lambda: NOW
    )
    collected = collector.collect(
        _loaded_formal(local_engine),
        MaaExecutionOutcome(
            False,
            "failed",
            {
                "modules": [
                    {
                        "result": {
                            "success": False,
                            "failure_screenshot": {
                                "path": str(tmp_path / "image.png"),
                                "mime": "image/png",
                                "data_base64": "private",
                            },
                        }
                    }
                ],
            },
        ),
    )
    result = collected.diagnostic["modules"][0]["result"]
    assert result["failure_screenshot_error"] == "artifact_stage_failed:OSError"
    assert "path" not in result["failure_screenshot"]
    assert "data_base64" not in result["failure_screenshot"]


def test_quick_test_capture_is_persisted(local_engine: Engine, tmp_path: Path) -> None:
    source = tmp_path / "runtime" / "evidence" / "quick.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"quick-test")
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        work = uow.work_items.add(
            kind=WorkItemKind.QUICK_TEST,
            remote_id=uuid4(),
            content_hash="c" * 64,
            target_device_id=uuid4(),
            available_at=NOW,
            payload={},
            now=NOW,
        )
    loaded = LoadedExecutionWork(
        work,
        None,
        30,
        None,
        uuid4(),
        NOW,
        False,
    )
    collector = ExecutionArtifactCollector(
        store=LocalArtifactStore(tmp_path),
        uow_factory=_uow_factory(local_engine),
        clock=lambda: NOW,
    )

    collected = collector.collect(
        loaded,
        MaaExecutionOutcome(
            False,
            "quick_test_failed",
            {"failure_screenshot": {"path": str(source), "mime": "image/png"}},
        ),
    )

    assert collected.diagnostic["failure_screenshot"]["artifact_kind"] == "screenshot"
    assert not source.exists()
    with local_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(LocalArtifactRow)) == 1
        row = connection.execute(select(LocalArtifactRow)).one()
        assert row.owner_kind == "quick_test"
        assert UUID(row.owner_id) == work.remote_id


def test_formal_recording_segments_are_staged_as_video_artifacts(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    source = tmp_path / "runtime" / "recordings" / "segment.mp4"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"mp4-segment")
    loaded = _loaded_formal(local_engine)
    collector = ExecutionArtifactCollector(
        store=LocalArtifactStore(tmp_path),
        uow_factory=_uow_factory(local_engine),
        clock=lambda: NOW,
    )

    collected = collector.collect(
        loaded,
        MaaExecutionOutcome(
            True,
            None,
            {
                "recording": {
                    "status": "captured",
                    "segments": [{"path": str(source), "mime": "video/mp4", "segment_index": 1}],
                }
            },
        ),
    )

    segment = collected.diagnostic["recording"]["segments"][0]
    assert segment["artifact_kind"] == "video"
    assert "recording" in segment["file_name"]
    with LocalUnitOfWork.from_engine(local_engine) as uow:
        artifact = uow.artifacts.get(UUID(segment["artifact_id"]))
    assert artifact is not None
    assert artifact.artifact_kind == "video"
    assert artifact.media_type == "video/mp4"
    assert not source.exists()


def test_artifact_file_name_replaces_file_system_unsafe_characters(
    local_engine: Engine,
    tmp_path: Path,
) -> None:
    source = tmp_path / "runtime" / "evidence" / "source.png"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"png-evidence")
    loaded = _loaded_formal(local_engine, script_name="国服:领取/体力?")
    collector = ExecutionArtifactCollector(
        store=LocalArtifactStore(tmp_path),
        uow_factory=_uow_factory(local_engine),
        clock=lambda: NOW,
    )

    collected = collector.collect(
        loaded,
        MaaExecutionOutcome(
            False,
            "image_not_matched",
            {
                "module_index": 1,
                "failed_step": {"number": 7},
                "failure_screenshot": {"path": str(source), "mime": "image/png"},
            },
        ),
    )

    assert collected.diagnostic["failure_screenshot"]["file_name"] == ("国服_领取_体力_-第07步.png")
