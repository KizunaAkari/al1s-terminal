"""Data exchanged between the native pipeline compilation passes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CompiledMaaTask:
    """A browser-authored script compiled to a MaaFramework pipeline override."""

    entry: str
    pipeline: dict[str, dict[str, Any]]
    image_dir: Path
    script_hash: str
    step_nodes: dict[int, list[str]]
    step_exits: dict[int, list[str]]
    node_steps: dict[str, int]
    active_packages: list[str]
    source_script: dict[str, Any]
    requires_ocr: bool = False
    requires_yolo: bool = False


@dataclass
class _StepPlan:
    candidates: list[Any]
    exits: list[str]
    nodes: list[str] = field(default_factory=list)
    incoming_timeout_ms: int = 20_000
    incoming_rate_limit_ms: int = 1_000


@dataclass
class _FailureRetryRoute:
    target_index: int
    max_retries: int
    process_script_name: str
    entry_name: str
    exhausted_name: str
    target_success_name: str
    recovery_success_name: str
    process_entry: str
    process_pipeline: dict[str, dict[str, Any]]

    @property
    def error_candidates(self) -> list[str]:
        return [self.entry_name, self.exhausted_name]


@dataclass(slots=True)
class CompilationContext:
    step_count: int
    prefix: str
    image_dir: Path
    pipeline: dict[str, dict[str, Any]] = field(default_factory=dict)
    step_nodes: dict[int, list[str]] = field(default_factory=dict)
    step_exits: dict[int, list[str]] = field(default_factory=dict)
    node_steps: dict[str, int] = field(default_factory=dict)
    active_packages: set[str] = field(default_factory=set)
    skip_guard_jumps: list[tuple[str, int]] = field(default_factory=list)
    global_candidates: list[Any] = field(default_factory=list)
    requires_ocr: bool = False
    requires_yolo: bool = False
