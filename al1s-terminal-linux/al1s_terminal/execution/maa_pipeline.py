"""Stable entrypoint; each compile runs independent node, control-flow and asset passes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from al1s_terminal.execution.failure_skip import wire_failure_skips
from al1s_terminal.execution.pipeline_builders import PipelineNames
from al1s_terminal.execution.pipeline_control_flow import PipelineControlFlowCompiler
from al1s_terminal.execution.pipeline_steps import STEP_COMPILERS, PipelineStepCompiler
from al1s_terminal.execution.pipeline_types import CompilationContext, _FailureRetryRoute, _StepPlan
from al1s_terminal.execution.pipeline_types import CompiledMaaTask as CompiledMaaTask
from al1s_terminal.execution.popup_guard import wrap_popup_guards
from al1s_terminal.execution.post_execution_wait import (
    apply_post_execution_wait,
    apply_pre_execution_wait,
)
from al1s_terminal.execution.repeated_click import wrap_repeated_clicks
from al1s_terminal.execution.screen_guard import guard_pipeline, screen_size
from al1s_terminal.execution.step_budget import wrap_step_budgets


class MaaPipelineCompiler(PipelineNames):
    """Translate the editor DSL into native MaaFramework nodes; never run actions here."""

    supported_actions = frozenset(STEP_COMPILERS)

    def __init__(self, workdir: str | Path):
        self.workdir = Path(workdir)
        self.cache_root = self.workdir / "maa" / "compiled"

    def compile(self, script: dict[str, Any]) -> CompiledMaaTask:
        steps = script.get("steps", [])
        if not isinstance(steps, list):
            raise ValueError("steps must be an array")
        canonical = json.dumps(script, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        script_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        bundle_dir = self.cache_root / script_hash
        image_dir, pipeline_dir = bundle_dir / "image", bundle_dir / "pipeline"
        image_dir.mkdir(parents=True, exist_ok=True)
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        context = CompilationContext(len(steps), f"Web_{script_hash}", image_dir)
        return _PipelineCompilation(context, self.compile).assemble(
            script, script_hash, pipeline_dir
        )


class _PipelineCompilation(PipelineStepCompiler, PipelineControlFlowCompiler):
    def assemble(
        self,
        script: dict[str, Any],
        script_hash: str,
        pipeline_dir: Path,
    ) -> CompiledMaaTask:
        steps = script.get("steps", [])
        global_candidates_by_step = self._compile_global_popups(
            script.get("global_popups", []),
            len(steps),
        )
        plans: list[_StepPlan] = []
        for index, step in enumerate(steps):
            self.context.global_candidates = list(global_candidates_by_step.get(index, []))
            plans.append(self._compile_step(index, step))

        failure_retry_routes = self._compile_failure_retry_routes(
            steps,
            plans,
            global_candidates_by_step,
        )

        end_name = self._name("End")
        self.context.pipeline[end_name] = self._node(
            recognition="DirectHit",
            action="DoNothing",
            next_nodes=[],
            role="end",
        )

        self._link_successors(plans, global_candidates_by_step, end_name, failure_retry_routes)
        self._link_skips(plans, global_candidates_by_step)
        root_name = self._make_root(
            plans, global_candidates_by_step, end_name, failure_retry_routes
        )
        entries = {index: self._entry_candidates(index, plans, global_candidates_by_step)
                   for index in range(len(steps))}
        entries[len(steps)] = [end_name]
        return self._finish(script, script_hash, pipeline_dir, root_name, entries)

    def _finish(
        self,
        script: dict[str, Any],
        script_hash: str,
        pipeline_dir: Path,
        root_name: str,
        entries: dict[int, list[Any]],
    ) -> CompiledMaaTask:
        steps = script.get("steps", [])
        wrap_repeated_clicks(self.context.pipeline, steps, screen_size(script))
        wrap_popup_guards(self.context.pipeline)
        guard_pipeline(self.context.pipeline, screen_size(script))
        self._map_orientation_checks()
        wrap_step_budgets(self.context.pipeline, steps, self.context.prefix)
        wire_failure_skips(self.context.pipeline, steps, self.context.prefix, entries,
                           self.context.node_steps, self.context.step_nodes)
        compiled_path = pipeline_dir / "compiled.json"
        compiled_path.write_text(
            json.dumps(self.context.pipeline, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return CompiledMaaTask(
            entry=root_name,
            pipeline=self.context.pipeline,
            image_dir=self.context.image_dir,
            script_hash=script_hash,
            step_nodes=self.context.step_nodes,
            step_exits=self.context.step_exits,
            node_steps=self.context.node_steps,
            active_packages=sorted(self.context.active_packages),
            source_script=dict(script),
            requires_ocr=self.context.requires_ocr,
            requires_yolo=self.context.requires_yolo,
        )

    def _map_orientation_checks(self) -> None:
        # The guard adds a real continuation after StartApp. It must finish
        # before the launch step can be reported complete or the next can start.
        for name, index in list(self.context.node_steps.items()):
            ready = name + "_OrientationReady"
            if ready not in self.context.pipeline:
                continue
            self.context.node_steps[ready] = index
            self.context.step_nodes[index].append(ready)
            self.context.step_exits[index] = [
                ready if exit_name == name else exit_name
                for exit_name in self.context.step_exits[index]
            ]

    def _link_successors(
        self,
        plans: list[_StepPlan],
        global_candidates_by_step: dict[int, list[str]],
        end_name: str,
        failure_retry_routes: dict[int, _FailureRetryRoute],
    ) -> None:
        for position, index in enumerate(range(len(plans))):
            next_index = position + 1 if position + 1 < len(plans) else None
            next_candidates = (
                self._entry_candidates(next_index, plans, global_candidates_by_step)
                if next_index is not None
                else [end_name]
            )
            next_timeout_ms = (
                plans[next_index].incoming_timeout_ms if next_index is not None else 1_000
            )
            next_rate_limit_ms = (
                plans[next_index].incoming_rate_limit_ms if next_index is not None else 100
            )
            for exit_name in self.context.step_exits[index]:
                node = self.context.pipeline[exit_name]
                node["next"] = list(next_candidates)
                node["timeout"] = next_timeout_ms
                node["rate_limit"] = next_rate_limit_ms
                if next_index in failure_retry_routes:
                    self._append_on_error(
                        node,
                        failure_retry_routes[next_index].error_candidates,
                    )

    def _link_skips(
        self,
        plans: list[_StepPlan],
        global_candidates_by_step: dict[int, list[str]],
    ) -> None:
        # A condition can jump over a selected contiguous range of later steps.
        # The guard is the first candidate for its step, so this branch is only
        # taken when the condition recognition hits; a miss follows the normal
        # next-step link.
        for guard_name, target_index in self.context.skip_guard_jumps:
            guard = self.context.pipeline[guard_name]
            target_candidates = self._entry_candidates(
                target_index,
                plans,
                global_candidates_by_step,
            )
            guard["next"] = list(target_candidates)
            guard["timeout"] = plans[target_index].incoming_timeout_ms
            guard["rate_limit"] = plans[target_index].incoming_rate_limit_ms

    def _make_root(
        self,
        plans: list[_StepPlan],
        global_candidates_by_step: dict[int, list[str]],
        end_name: str,
        failure_retry_routes: dict[int, _FailureRetryRoute],
    ) -> str:
        root_name = self._name("Root")
        first_index = 0 if plans else None
        first_candidates = (
            self._entry_candidates(first_index, plans, global_candidates_by_step)
            if first_index is not None
            else [end_name]
        )
        self.context.pipeline[root_name] = self._node(
            recognition="DirectHit",
            action="DoNothing",
            next_nodes=first_candidates,
            role="root",
        )
        self.context.pipeline[root_name]["timeout"] = (
            plans[first_index].incoming_timeout_ms if first_index is not None else 1_000
        )
        self.context.pipeline[root_name]["rate_limit"] = (
            plans[first_index].incoming_rate_limit_ms if first_index is not None else 100
        )
        if first_index in failure_retry_routes:
            self._append_on_error(
                self.context.pipeline[root_name],
                failure_retry_routes[first_index].error_candidates,
            )

        return root_name

    def _compile_step(self, index: int, raw_step: Any) -> _StepPlan:
        if not isinstance(raw_step, dict):
            raise ValueError(f"step {index + 1} must be an object")
        step = raw_step
        action = str(step.get("action") or "")
        if not action:
            raise ValueError(f"step {index + 1} is missing action")

        compiler = STEP_COMPILERS.get(action)
        if not compiler:
            raise ValueError(f"MaaFramework backend does not support action: {action}")
        plan: _StepPlan = compiler(self, index, step)
        apply_pre_execution_wait(self.context.pipeline, plan.candidates, step)
        apply_post_execution_wait(self.context.pipeline, plan.exits, step)

        assertion = step.get("post_assertion")
        if isinstance(assertion, dict) and assertion.get("enabled") is True:
            plan = self._wrap_post_assertion(index, assertion, plan)

        condition = step.get("skip_condition")
        if isinstance(condition, dict) and condition.get("enabled") is True and action != "start":
            mode = str(condition.get("mode") or "numeric")
            if mode == "numeric":
                plan = self._wrap_numeric_skip(index, condition, plan)
            elif mode == "image":
                plan = self._wrap_image_skip(index, condition, plan)
            elif mode not in {"recognition_failure", "execution_failure"}:
                raise ValueError("skip condition mode is unsupported")

        self.context.step_nodes[index] = list(plan.nodes)
        self.context.step_exits[index] = list(plan.exits)
        for node_name in plan.nodes:
            self.context.node_steps[node_name] = index
        return plan
