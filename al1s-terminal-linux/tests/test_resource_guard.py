from __future__ import annotations

from pathlib import Path

import pytest

from al1s_terminal.providers.resource_guard import (
    ResourceSnapshot,
    RuntimeResourceGuard,
)

GIB = 1024**3
MIB = 1024**2


def _guard(tmp_path: Path, snapshot: ResourceSnapshot) -> RuntimeResourceGuard:
    return RuntimeResourceGuard(
        data_dir=tmp_path,
        minimum_available_memory_bytes=256 * MIB,
        minimum_available_storage_bytes=GIB,
        recording_storage_reserve_bytes=512 * MIB,
        probe=lambda: snapshot,
    )


def test_matching_low_resource_snapshot_is_permitted(tmp_path: Path) -> None:
    decision = _guard(
        tmp_path,
        ResourceSnapshot(
            available_memory_bytes=512 * MIB,
            available_storage_bytes=2 * GIB,
            architecture="aarch64",
        ),
    ).evaluate(
        {"architectures": ["aarch64"], "min_storage_bytes": 256 * MIB},
        record_video=False,
    )

    assert decision.permitted is True
    assert decision.code is None


def test_memory_pressure_blocks_without_consuming_work(tmp_path: Path) -> None:
    decision = _guard(
        tmp_path,
        ResourceSnapshot(
            available_memory_bytes=128 * MIB,
            available_storage_bytes=10 * GIB,
            architecture="aarch64",
        ),
    ).evaluate({}, record_video=False)

    assert decision.permitted is False
    assert decision.code == "terminal_memory_pressure"


def test_recording_reserve_is_added_to_storage_floor(tmp_path: Path) -> None:
    guard = _guard(
        tmp_path,
        ResourceSnapshot(
            available_memory_bytes=512 * MIB,
            available_storage_bytes=1400 * MIB,
            architecture="aarch64",
        ),
    )

    without_video = guard.evaluate({}, record_video=False)
    with_video = guard.evaluate({}, record_video=True)

    assert without_video.permitted is True
    assert with_video.permitted is False
    assert with_video.code == "terminal_storage_pressure"
    assert with_video.diagnostic["required_available_storage_bytes"] == 1536 * MIB


def test_architecture_drift_and_invalid_requirements_fail_closed(tmp_path: Path) -> None:
    guard = _guard(
        tmp_path,
        ResourceSnapshot(
            available_memory_bytes=GIB, available_storage_bytes=10 * GIB, architecture="x86_64"
        ),
    )

    decision = guard.evaluate({"architectures": ["aarch64"]}, record_video=False)
    assert decision.code == "terminal_architecture_changed"
    with pytest.raises(ValueError, match="non-negative integer"):
        guard.evaluate({"min_storage_bytes": -1}, record_video=False)


def test_task_memory_requirement_can_raise_the_local_floor(tmp_path: Path) -> None:
    guard = _guard(
        tmp_path,
        ResourceSnapshot(
            available_memory_bytes=300 * MIB,
            available_storage_bytes=10 * GIB,
            architecture="aarch64",
        ),
    )

    decision = guard.evaluate({"min_memory_bytes": 400 * MIB}, record_video=False)

    assert decision.permitted is False
    assert decision.code == "terminal_memory_pressure"
    assert decision.diagnostic["required_available_memory_bytes"] == 400 * MIB
