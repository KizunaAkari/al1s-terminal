from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from al1s_terminal.execution.maa_definition import MaaExecutionPlan
from al1s_terminal.execution.maa_pipeline import CompiledMaaTask
from al1s_terminal.execution.maa_plan_compiler import MaaPlanCompiler
from al1s_terminal.execution.rule_event_codes import bind_module_events


class MaaRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str, diagnostic: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.diagnostic = dict(diagnostic or {})


@dataclass(frozen=True, slots=True)
class PreparedMaaExecution:
    plan: MaaExecutionPlan
    tasks: tuple[CompiledMaaTask, ...]


@dataclass(frozen=True, slots=True)
class MaaExecutionOutcome:
    passed: bool
    error_code: str | None
    diagnostic: dict[str, Any]


class MaaTaskRunner(Protocol):
    def run(
        self,
        task: CompiledMaaTask,
        *,
        adb_serial: str,
        timeout_seconds: int,
        capture_failure: bool,
        cancel_requested: Callable[[], bool] | None = None,
        on_event: Callable[[str, int], None] | None = None,
    ) -> dict[str, Any]: ...


class MaaExecutionEngine:
    def __init__(
        self,
        *,
        compiler: MaaPlanCompiler,
        runner: MaaTaskRunner,
        monotonic: Callable[[], float] = time.monotonic,
        wait: Callable[[float], None] = time.sleep,
    ) -> None:
        self._compiler = compiler
        self._runner = runner
        self._monotonic = monotonic
        self._wait = wait

    def prepare(self, plan: MaaExecutionPlan) -> PreparedMaaExecution:
        return PreparedMaaExecution(plan=plan, tasks=self._compiler.compile(plan))

    def execute(
        self,
        prepared: PreparedMaaExecution,
        *,
        adb_serial: str,
        timeout_seconds: int,
        capture_failure: bool = False,
        cancel_requested: Callable[[], bool] | None = None,
        on_event: Callable[[str, int], None] | None = None,
    ) -> MaaExecutionOutcome:
        try:
            outcome = self._execute_modules(
                prepared,
                adb_serial=adb_serial,
                timeout_seconds=timeout_seconds,
                capture_failure=capture_failure,
                cancel_requested=cancel_requested,
                on_event=on_event,
            )
        except MaaRuntimeError as exc:
            diagnostic = dict(exc.diagnostic)
            self._finish_failed_strategy(prepared, diagnostic, adb_serial, capture_failure)
            raise MaaRuntimeError(exc.code, str(exc), diagnostic) from exc
        if not outcome.passed:
            self._finish_failed_strategy(prepared, outcome.diagnostic, adb_serial, capture_failure)
        return outcome

    def _finish_failed_strategy(
        self,
        prepared: PreparedMaaExecution,
        diagnostic: dict[str, Any],
        adb_serial: str,
        capture_failure: bool,
    ) -> None:
        # Single-step/quick-test executions must never run an unrelated end script.
        if prepared.plan.definition_type != "strategy":
            return
        attempted = int(diagnostic.get("module_index") or len(diagnostic.get("modules", [])))
        deadline = self._monotonic() + 60
        for index, (module, task) in enumerate(
            zip(prepared.plan.modules, prepared.tasks, strict=True), 1
        ):
            if index <= attempted or module.script_type != "module_end":
                continue
            remaining = max(0, int(deadline - self._monotonic()))
            if not remaining:
                diagnostic["end_script_cleanup"] = {"status": "timeout"}
                return
            try:
                result = self._runner.run(
                    task,
                    adb_serial=adb_serial,
                    timeout_seconds=remaining,
                    capture_failure=capture_failure,
                    cancel_requested=None,
                )
                diagnostic["end_script_cleanup"] = {
                    "status": "completed" if result.get("success") is True else "failed",
                    "module_index": index,
                    "result": result,
                }
            except Exception as exc:
                diagnostic["end_script_cleanup"] = {
                    "status": "failed",
                    "module_index": index,
                    "error_code": getattr(exc, "code", "maa_runtime_failed"),
                    "result": getattr(exc, "diagnostic", {}),
                }
            return

    def _execute_modules(
        self,
        prepared: PreparedMaaExecution,
        *,
        adb_serial: str,
        timeout_seconds: int,
        capture_failure: bool = False,
        cancel_requested: Callable[[], bool] | None = None,
        on_event: Callable[[str, int], None] | None = None,
    ) -> MaaExecutionOutcome:
        started = self._monotonic()
        modules: list[dict[str, Any]] = []
        for index, (module, task) in enumerate(
            zip(prepared.plan.modules, prepared.tasks, strict=True), start=1
        ):
            if cancel_requested is not None and cancel_requested():
                raise MaaRuntimeError("execution_cancelled", "Maa execution was cancelled")
            remaining = timeout_seconds - int(self._monotonic() - started)
            if remaining <= 0:
                raise MaaRuntimeError("execution_timeout", "Maa execution timed out")
            try:
                run_options: dict[str, Any] = {
                    "adb_serial": adb_serial,
                    "timeout_seconds": remaining,
                    "capture_failure": capture_failure,
                    "cancel_requested": cancel_requested,
                }
                if on_event is not None:
                    run_options["on_event"] = bind_module_events(on_event, module.definition_key)
                result = self._runner.run(task, **run_options)
            except MaaRuntimeError as exc:
                raise MaaRuntimeError(
                    exc.code,
                    str(exc),
                    {
                        **exc.diagnostic,
                        "module_index": index,
                        "definition_key": module.definition_key,
                    },
                ) from exc
            except Exception as exc:
                raise MaaRuntimeError(
                    "maa_runtime_failed",
                    "MaaFramework pipeline execution failed",
                    {"module_index": index, "detail": str(exc)[:512]},
                ) from exc
            modules.append(
                {
                    "module_index": index,
                    "definition_key": module.definition_key,
                    "script_name": module.script_name,
                    "result": result,
                }
            )
            if result.get("success") is not True:
                return MaaExecutionOutcome(
                    passed=False,
                    error_code=str(result.get("error_code") or "maa_pipeline_failed")[:100],
                    diagnostic={"modules": modules},
                )
            if module.wait_after_ms > 0 and index < len(prepared.tasks):
                self._wait_between_modules(
                    module.wait_after_ms / 1_000,
                    cancel_requested=cancel_requested,
                )
        return MaaExecutionOutcome(
            passed=True,
            error_code=None,
            diagnostic={"modules": modules},
        )

    def _wait_between_modules(
        self,
        seconds: float,
        *,
        cancel_requested: Callable[[], bool] | None,
    ) -> None:
        deadline = self._monotonic() + seconds
        while self._monotonic() < deadline:
            if cancel_requested is not None and cancel_requested():
                raise MaaRuntimeError("execution_cancelled", "Maa execution was cancelled")
            self._wait(min(0.25, max(0.0, deadline - self._monotonic())))
