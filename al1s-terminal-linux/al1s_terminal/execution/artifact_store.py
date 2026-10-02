from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from al1s_terminal.execution.capture_budget import MAX_SCREENSHOTS_PER_SCRIPT
from al1s_terminal.execution.maa_runtime import MaaExecutionOutcome
from al1s_terminal.execution.work_item_loader import LoadedExecutionWork
from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork
from al1s_terminal.types import NewLocalArtifact, WorkItemKind

ARTIFACT_NAMESPACE = UUID("52bf30de-3307-45fc-bae1-f4b944ae3ec4")


class LocalArtifactStore:
    def __init__(self, data_dir: Path) -> None:
        self._root = data_dir.resolve()
        self._artifact_root = self._root / "artifacts"

    def stage(
        self,
        *,
        artifact_id: UUID,
        work_item_id: UUID,
        owner_kind: str,
        owner_id: UUID,
        artifact_kind: str,
        file_name: str,
        media_type: str,
        source: Path,
    ) -> NewLocalArtifact:
        source = source.resolve(strict=True)
        if not source.is_relative_to(self._root) or source.is_symlink() or not source.is_file():
            raise ValueError("artifact source is outside the controlled data directory")
        safe_name = _safe_file_name(file_name)
        suffix = Path(safe_name).suffix[:16]
        relative_path = (
            Path("artifacts") / str(work_item_id) / f"{artifact_id}{suffix}"
        ).as_posix()
        target = self._controlled_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.is_symlink() or not target.is_file():
                raise ValueError("artifact destination is not a regular file")
            size_bytes, sha256 = _file_identity(target)
        else:
            temporary = target.with_name(f".{artifact_id}.tmp")
            if temporary.exists() or temporary.is_symlink():
                raise ValueError("artifact temporary path already exists")
            size_bytes, sha256 = _copy_and_hash(source, temporary)
            os.replace(temporary, target)
            _fsync_parent(target)
        return NewLocalArtifact(
            artifact_id=artifact_id,
            work_item_id=work_item_id,
            owner_kind=owner_kind,
            owner_id=owner_id,
            artifact_kind=artifact_kind,
            file_name=safe_name,
            relative_path=relative_path,
            sha256=sha256,
            size_bytes=size_bytes,
            media_type=media_type,
        )

    def path_for(self, relative_path: str) -> Path:
        path = self._controlled_path(relative_path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("artifact path is not a regular file")
        return path

    def delete(self, relative_path: str) -> None:
        path = self._controlled_path(relative_path)
        if not path.exists():
            return
        if path.is_symlink() or not path.is_file():
            raise ValueError("artifact path is not a regular file")
        path.unlink()
        _fsync_parent(path)

    def discard_runtime_source(self, source: Path) -> None:
        runtime_root = (self._root / "runtime").resolve(strict=False)
        path = source.resolve(strict=False)
        if not path.is_relative_to(runtime_root):
            return
        if not path.exists():
            return
        if path.is_symlink() or not path.is_file():
            raise ValueError("runtime capture path is not a regular file")
        path.unlink()
        _fsync_parent(path)

    def _controlled_path(self, relative_path: str) -> Path:
        if Path(relative_path).is_absolute() or ".." in Path(relative_path).parts:
            raise ValueError("artifact path escaped the controlled data directory")
        path = self._root.joinpath(*Path(relative_path).parts).resolve(strict=False)
        if not path.is_relative_to(self._artifact_root.resolve(strict=False)):
            raise ValueError("artifact path escaped the artifact directory")
        return path


class ExecutionArtifactCollector:
    def __init__(
        self,
        *,
        store: LocalArtifactStore,
        uow_factory: Callable[[], LocalUnitOfWork],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._uow_factory = uow_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def collect(
        self,
        loaded: LoadedExecutionWork,
        outcome: MaaExecutionOutcome,
    ) -> MaaExecutionOutcome:
        diagnostic = _copy_json(outcome.diagnostic)
        captures: list[tuple[dict[str, Any], tuple[str, ...]]] = []
        _find_artifacts(diagnostic, (), captures)
        if loaded.work_item.kind is WorkItemKind.FORMAL_TASK and loaded.package is None:
            for capture, _location in captures:
                with suppress(OSError, ValueError, KeyError):
                    self._store.discard_runtime_source(Path(str(capture["path"])))
            return replace(outcome, diagnostic=_strip_local_capture_data(diagnostic))

        artifacts: list[NewLocalArtifact] = []
        staged_sources: list[Path] = []
        errors: list[str] = []
        selected, discarded = _select_captures(captures)
        aliases: dict[str, list[dict[str, Any]]] = {}
        for capture, _location in captures:
            aliases.setdefault(str(Path(capture["path"]).resolve()), []).append(capture)
        for capture, _location in discarded:
            source = Path(capture["path"])
            for alias in aliases[str(source.resolve())]:
                alias.clear()
                alias["discarded"] = "screenshot_limit"
            with suppress(OSError, ValueError):
                self._store.discard_runtime_source(source)
        if discarded:
            diagnostic["discarded_screenshot_count"] = len(discarded)
        for ordinal, (capture, location) in enumerate(selected, start=1):
            try:
                source = Path(str(capture["path"]))
                artifact_id = uuid5(
                    ARTIFACT_NAMESPACE,
                    f"{loaded.work_item.work_item_id}:{source}:{ordinal}",
                )
                file_name = _capture_file_name(loaded, diagnostic, location, ordinal, source)
                staged = self._store.stage(
                    artifact_id=artifact_id,
                    work_item_id=loaded.work_item.work_item_id,
                    owner_kind="formal_attempt" if loaded.package else "quick_test",
                    owner_id=(loaded.package.attempt_id if loaded.package
                              else loaded.work_item.remote_id),
                    artifact_kind=_artifact_kind(capture),
                    file_name=file_name,
                    media_type=str(capture.get("mime") or "image/png"),
                    source=source,
                )
                artifacts.append(staged)
                staged_sources.append(source)
                reference = {
                        "artifact_id": str(artifact_id),
                        "artifact_kind": staged.artifact_kind,
                        "file_name": staged.file_name,
                }
                for alias in aliases[str(source.resolve())]:
                    alias.clear()
                    alias.update(reference)
            except (OSError, ValueError, KeyError) as exc:
                errors.append(str(exc)[:512])
                capture.pop("data_base64", None)
                capture.pop("path", None)
                if location and location[-1] == "failure_screenshot":
                    parent: Any = diagnostic
                    for part in location[:-1]:
                        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
                    parent["failure_screenshot_error"] = (
                        f"artifact_stage_failed:{type(exc).__name__}"
                    )
        if artifacts:
            with self._uow_factory() as uow:
                uow.artifacts.add_many(tuple(artifacts), now=self._clock())
            for source in staged_sources:
                with suppress(OSError, ValueError):
                    self._store.discard_runtime_source(source)
        if errors:
            diagnostic["artifact_errors"] = errors
        return replace(outcome, diagnostic=_strip_local_capture_data(diagnostic))


def _find_artifacts(
    value: Any,
    location: tuple[str, ...],
    captures: list[tuple[dict[str, Any], tuple[str, ...]]],
) -> None:
    if isinstance(value, dict):
        if isinstance(value.get("path"), str) and _supported_media(value.get("mime")):
            captures.append((value, location))
            return
        for key, child in value.items():
            _find_artifacts(child, (*location, str(key)), captures)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _find_artifacts(child, (*location, str(index)), captures)


def _select_captures(
    captures: list[tuple[dict[str, Any], tuple[str, ...]]],
) -> tuple[
    list[tuple[dict[str, Any], tuple[str, ...]]],
    list[tuple[dict[str, Any], tuple[str, ...]]],
]:
    """Deduplicate mirrored diagnostics, then reserve each script's failure evidence."""
    unique: dict[str, tuple[dict[str, Any], tuple[str, ...]]] = {}
    for capture, location in captures:
        key = str(Path(capture["path"]).resolve())
        previous = unique.get(key)
        if previous is None or "failure_screenshot" in location:
            unique[key] = capture, location
    selected: list[tuple[dict[str, Any], tuple[str, ...]]] = []
    discarded: list[tuple[dict[str, Any], tuple[str, ...]]] = []
    counts: dict[str, int] = {}
    ordered = sorted(unique.values(), key=lambda item: "failure_screenshot" not in item[1])
    for capture, location in ordered:
        group = location[1] if len(location) > 1 and location[0] == "modules" else "root"
        if _artifact_kind(capture) == "video":
            selected.append((capture, location))
        elif counts.get(group, 0) < MAX_SCREENSHOTS_PER_SCRIPT:
            counts[group] = counts.get(group, 0) + 1
            selected.append((capture, location))
        else:
            discarded.append((capture, location))
    return selected, discarded


def _capture_file_name(
    loaded: LoadedExecutionWork,
    diagnostic: dict[str, Any],
    location: tuple[str, ...],
    ordinal: int,
    source: Path,
) -> str:
    context = diagnostic
    module_info: dict[str, Any] = {}
    if len(location) >= 3 and location[0] == "modules":
        modules = diagnostic.get("modules")
        if isinstance(modules, list) and location[1].isdigit():
            index = int(location[1])
            if index < len(modules) and isinstance(modules[index], dict):
                module_info = modules[index]
                result = module_info.get("result")
                if isinstance(result, dict):
                    context = result
    failed_step = context.get("failed_step")
    number = failed_step.get("number") if isinstance(failed_step, dict) else None
    script_name = _artifact_script_name(loaded, module_info or diagnostic)
    media_kind = _artifact_kind_from_mime(_mime_at(diagnostic, location))
    if media_kind == "video":
        stem = f"{script_name}-recording-{ordinal:02d}"
    else:
        if "failure_screenshot" in location and isinstance(number, int):
            stem = f"{script_name}-第{number:02d}步"
        else:
            stem = f"{script_name}-capture-{ordinal:02d}"
    suffix = source.suffix if source.suffix else ".bin"
    return _safe_file_name(f"{stem}{suffix}")


def _artifact_script_name(
    loaded: LoadedExecutionWork,
    diagnostic: dict[str, Any],
) -> str:
    if loaded.plan is None or not loaded.plan.modules:
        return "script"
    module_index = diagnostic.get("module_index")
    if isinstance(module_index, int) and 1 <= module_index <= len(loaded.plan.modules):
        return loaded.plan.modules[module_index - 1].script_name
    return loaded.plan.modules[0].script_name


def _supported_media(value: object) -> bool:
    return isinstance(value, str) and (value.startswith("image/") or value == "video/mp4")


def _artifact_kind(capture: dict[str, Any]) -> str:
    return _artifact_kind_from_mime(capture.get("mime"))


def _artifact_kind_from_mime(value: object) -> str:
    return "video" if value == "video/mp4" else "screenshot"


def _mime_at(value: object, location: tuple[str, ...]) -> object:
    current = value
    for part in location:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current.get("mime") if isinstance(current, dict) else None


def _copy_json(value: dict[str, Any]) -> dict[str, Any]:
    copied = _json_value(value)
    return copied if isinstance(copied, dict) else {}


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_json_value(child) for child in value]
    if isinstance(value, tuple):
        return [_json_value(child) for child in value]
    return value


def _strip_local_capture_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_local_capture_data(child)
            for key, child in value.items()
            if key not in {"data_base64", "path"}
        }
    if isinstance(value, list):
        return [_strip_local_capture_data(child) for child in value]
    return value


def _safe_file_name(value: str) -> str:
    normalized = "".join(
        "_" if character in '<>:"/\\|?*' or ord(character) < 32 else character
        for character in value.strip()
    ).rstrip(" .")
    if not normalized:
        raise ValueError("artifact file name is invalid")
    if len(normalized) <= 255:
        return normalized
    suffix = Path(normalized).suffix[:16]
    return f"{Path(normalized).stem[: 255 - len(suffix)]}{suffix}"


def _copy_and_hash(source: Path, target: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with source.open("rb") as input_stream, os.fdopen(descriptor, "wb", closefd=True) as output:
            while chunk := input_stream.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return size, digest.hexdigest()


def _file_identity(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _fsync_parent(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
