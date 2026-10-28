from __future__ import annotations

# User-facing Chinese diagnostics intentionally use full-width punctuation.
# ruff: noqa: RUF001
import json
import math
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .color_marker import find_color_markers
from .custom_extensions import register_custom_extensions
from .maa_diagnostics import MaaDiagnostics
from .maa_pipeline import CompiledMaaTask, MaaPipelineCompiler
from .maa_runtime_types import MaaExecutionError as MaaExecutionError
from .maa_runtime_types import YoloAdapter as YoloAdapter
from .recognition_diagnostics import (
    enrich_execution_failure,
    enrich_recognition_failure,
    install_recognition_sink,
)


class MaaAdapter(MaaDiagnostics):
    """MaaFramework runtime for the terminal's browser-authored test scripts."""

    def __init__(
        self,
        device: Any,
        yolo: YoloAdapter | None = None,
        *,
        adb_path: str | Path = "adb",
        ocr_model_dir: Path | None = None,
        adb_screencap_methods: int | None = None,
        adb_input_methods: int | None = None,
        screenshot_mode: str = "raw",
        screenshot_short_side: int = 720,
    ):
        self.device = device
        self.workdir = Path(getattr(device, "workdir", "./agent-data"))
        self.compiler = MaaPipelineCompiler(self.workdir)
        self.yolo = yolo or YoloAdapter()
        self.adb_path = str(adb_path)
        self._ocr_model_dir = ocr_model_dir or self.workdir / "maa" / "resource" / "model" / "ocr"
        self._adb_screencap_methods = adb_screencap_methods
        self._adb_input_methods = adb_input_methods
        self._screenshot_mode = screenshot_mode.strip().lower()
        if self._screenshot_mode not in {"raw", "scaled"}:
            raise ValueError("screenshot_mode must be raw or scaled")
        self._screenshot_short_side = max(360, screenshot_short_side)
        self.available = False
        self.framework_version: str | None = None
        self.last_error = "MaaFw is not installed"
        self.controller: Any = None
        self._controller_address = ""
        self._maa: dict[str, Any] = {}
        self._initialize_binding()

    def _initialize_binding(self) -> None:
        try:
            from maa.controller import AdbController
            from maa.custom_action import CustomAction
            from maa.custom_recognition import CustomRecognition
            from maa.library import Library
            from maa.pipeline import JOCR, JRecognitionType, JTemplateMatch
            from maa.resource import Resource
            from maa.tasker import Tasker
            from maa.toolkit import Toolkit

            maa_user_dir = self.workdir / "maa" / "user"
            maa_user_dir.mkdir(parents=True, exist_ok=True)
            if not Toolkit.init_option(
                maa_user_dir,
                {
                    "logging": True,
                    "save_on_error": True,
                    "save_draw": False,
                    "stdout_level": 2,
                },
            ):
                raise RuntimeError("MaaFramework Toolkit.init_option failed")
            self._maa = {
                "AdbController": AdbController,
                "CustomAction": CustomAction,
                "CustomRecognition": CustomRecognition,
                "JOCR": JOCR,
                "JRecognitionType": JRecognitionType,
                "JTemplateMatch": JTemplateMatch,
                "Library": Library,
                "Resource": Resource,
                "Tasker": Tasker,
                "Toolkit": Toolkit,
            }
            self.framework_version = Library.version()
            self.available = True
            self.last_error = ""
        except Exception as exc:
            self.available = False
            self.last_error = str(exc)

    @property
    def ocr_model_dir(self) -> Path:
        return self._ocr_model_dir

    @property
    def ocr_available(self) -> bool:
        path = self.ocr_model_dir
        return all((path / filename).is_file() for filename in ("det.onnx", "rec.onnx", "keys.txt"))

    def status(self) -> dict[str, Any]:
        controller_info: dict[str, Any] | None = None
        if self.controller is not None:
            try:
                controller_info = self.controller.info
            except Exception:
                controller_info = None
        return {
            "available": self.available,
            "version": self.framework_version,
            "connected": bool(self.controller is not None and self.controller.connected),
            "controller": controller_info,
            "screenshot_mode": self._screenshot_mode,
            "ocr_available": self.ocr_available,
            "ocr_model_dir": str(self.ocr_model_dir),
            "error": self.last_error or None,
        }

    def run(
        self,
        script: dict[str, Any],
        _params: dict[str, Any],
        *,
        compiled_task: CompiledMaaTask | None = None,
        on_step_event: Callable[[str, int], None] | None = None,
    ) -> dict[str, Any]:
        if not self.available:
            raise MaaExecutionError(
                f"MaaFramework is unavailable: {self.last_error}",
                {
                    "success": False,
                    "error": f"MaaFramework is unavailable: {self.last_error}",
                    "error_type": "MaaFrameworkUnavailable",
                    "backend": "maafw",
                },
            )

        started = time.monotonic()
        compiled = compiled_task or self.compiler.compile(script)
        if compiled.requires_yolo and not self.yolo.available:
            raise MaaExecutionError(
                f"YOLO custom recognizer is unavailable: {self.yolo.error}",
                {
                    "success": False,
                    "error": f"YOLO custom recognizer is unavailable: {self.yolo.error}",
                    "error_type": "YoloProviderUnavailable",
                    "backend": "maafw",
                },
            )
        if compiled.requires_ocr and not self.ocr_available:
            raise MaaExecutionError(
                f"MaaFramework OCR model is incomplete: {self.ocr_model_dir}",
                {
                    "success": False,
                    "error": f"MaaFramework OCR model is incomplete: {self.ocr_model_dir}",
                    "error_type": "MaaOcrModelUnavailable",
                    "backend": "maafw",
                },
            )

        from al1s_terminal.execution.repeated_click import (
            has_repeated_click,
            register_repeated_click,
        )
        from al1s_terminal.execution.screen_guard import (
            register_guard,
            requires_guard,
            wait_guarded,
        )

        has_repeats = has_repeated_click(compiled.pipeline)
        has_guard = requires_guard(compiled.pipeline)
        if has_guard and self._screenshot_mode != "raw":
            raise MaaExecutionError(
                "Bound script requires native screenshot size",
                {
                    "success": False,
                    "error_type": "MaaScreenSizeMismatch",
                },
            )
        controller = self._ensure_controller()
        resource = self._maa["Resource"]()
        tasker = self._maa["Tasker"]()
        collector: dict[str, list[dict[str, Any]]] = {
            "custom_actions": [],
            "numeric_conditions": [],
            "yolo": [],
            "color_markers": [],
            "feedback": [],
            "conditional_skip_captures": [],
        }

        if any(compiled.image_dir.iterdir()):
            self._require_job(resource.post_image(compiled.image_dir), "load compiled image assets")
        if compiled.requires_ocr:
            self._require_job(resource.post_ocr_model(self.ocr_model_dir), "load Maa OCR model")
        from al1s_terminal.execution.action_dispatch import RegisteredActions

        registrations = RegisteredActions(resource)
        register_custom_extensions(
            registrations,
            collector,
            device=self.device,
            yolo=self.yolo,
            maa=self._maa,
            parse_json=self._json_object,
            first_number=self._first_number,
            rect_tuple=self._rect_tuple,
            find_color_markers=find_color_markers,
        )
        screen_failures: list[dict[str, Any]] = []
        from al1s_terminal.execution.popup_guard import has_popup_guard, register_popup_guard

        has_popups = has_popup_guard(compiled.pipeline)
        from al1s_terminal.execution.step_budget import has_step_budget, register_step_budgets

        has_budgets = has_step_budget(compiled.pipeline)
        budget_clock = None
        if has_budgets:
            budget_clock = register_step_budgets(
                registrations,
                self._maa["CustomRecognition"],
                self._maa["CustomAction"],
                screen_failures,
            )
            for node in compiled.pipeline.values():
                if node.get("custom_recognition") == "Al1sStepBudgetRecognition":
                    config = node["custom_recognition_param"]
                    if config["step_index"] == 0 and not config["rule"]:
                        budget_clock.enter(config["key"])
                        budget_clock.budget = config["budget"]
                        break
        if has_popups:
            register_popup_guard(
                registrations,
                self._maa["CustomRecognition"],
                self._maa["CustomAction"],
                screen_failures,
            )
        if has_repeats:
            register_repeated_click(
                registrations, self._maa["CustomAction"], screen_failures, budget_clock
            )
        if has_guard:
            register_guard(resource, self._maa["CustomRecognition"], screen_failures)

        if not tasker.bind(resource, controller) or not tasker.inited:
            raise MaaExecutionError("MaaFramework Tasker initialization failed")

        from al1s_terminal.execution.capture_budget import CaptureBudget
        from al1s_terminal.execution.failure_skip import register_failure_skips

        failure_state = register_failure_skips(
            registrations, tasker, self._maa, compiled, collector, screen_failures,
            budget_clock,
            CaptureBudget(getattr(self.device, "capture_evidence", self.device.screenshot)),
            on_step_event,
        )
        if on_step_event is not None:
            from al1s_terminal.execution.debug_events import install_step_sink

            install_step_sink(tasker, compiled.node_steps, compiled.step_exits, on_step_event,
                              pipeline=compiled.pipeline)
        recognition_tracker = install_recognition_sink(tasker, compiled.pipeline)

        job = tasker.post_task(compiled.entry, compiled.pipeline)
        if has_guard or has_repeats or has_popups or has_budgets:
            wait_guarded(
                tasker,
                job,
                screen_failures,
                (lambda: budget_clock.watchdog(screen_failures))
                if budget_clock is not None
                else None,
            )
        else:
            job.wait()
        detail = job.get()
        if screen_failures:
            guard_diagnostic = {
                "success": False,
                "error_type": screen_failures[0].get("error_type", "MaaScreenSizeMismatch"),
                "screen_size": screen_failures[0],
            }
            enrich_execution_failure(guard_diagnostic, compiled.pipeline, budget_clock)
            enrich_recognition_failure(guard_diagnostic, recognition_tracker, budget_clock)
            raise MaaExecutionError(
                "Execution guard stopped the task",
                guard_diagnostic,
            )
        summary = self._summarize(compiled, script, detail, collector)
        summary.update(
            {
                "backend": "maafw",
                "maa_version": self.framework_version,
                "duration_seconds": round(time.monotonic() - started, 3),
                "script_hash": compiled.script_hash,
                "active_packages": compiled.active_packages,
                "success": bool(job.succeeded),
            }
        )
        if not job.succeeded:
            summary["error"] = "MaaFramework pipeline execution failed"
            summary["error_type"] = "MaaPipelineFailed"
            summary["failed_step"] = self._find_failed_step(compiled, detail)
            if failure_state is not None and failure_state.failure is not None:
                actual_failure = failure_state.failure
                summary["failed_step"].update(index=actual_failure["step_index"],
                                              number=actual_failure["step_index"] + 1,
                                              node=actual_failure["node"])
            failed_index = summary["failed_step"].get("index")
            failed_node = str(summary["failed_step"].get("node") or "")
            node_prefix = f"Web_{compiled.script_hash}_"
            failure_retry_limit = self._failure_retry_limit(
                collector["custom_actions"],
                node_prefix=node_prefix,
            )
            failure_retry_process = self._failure_retry_process_failure(
                collector["custom_actions"],
                node_prefix=node_prefix,
            )
            if failure_retry_limit is not None:
                target_index = failure_retry_limit.get("target_step_index")
                max_retries = failure_retry_limit.get("max_retries")
                if isinstance(target_index, int):
                    failed_index = target_index
                    summary["failed_step"]["index"] = target_index
                    summary["failed_step"]["number"] = target_index + 1
                summary["error"] = (
                    f"步骤 #{int(failed_index) + 1:02d} 在失败恢复后仍未成功"
                    f"，已达到 {max_retries or '设定'} 次重试上限"
                    if isinstance(failed_index, int)
                    else "失败恢复次数已达到上限，原步骤仍未成功"
                )
                summary["error_type"] = "FailureRetryLimitExceeded"
            elif failure_retry_process is not None:
                target_index = failure_retry_process.get("target_step_index")
                script_name = str(failure_retry_process.get("process_script_name") or "过程脚本")
                if isinstance(target_index, int):
                    failed_index = target_index
                    summary["failed_step"]["index"] = target_index
                    summary["failed_step"]["number"] = target_index + 1
                summary["error"] = (
                    f"步骤 #{int(failed_index) + 1:02d} 失败后，"
                    f"恢复过程脚本【{script_name}】执行失败"
                    if isinstance(failed_index, int)
                    else f"失败恢复过程脚本【{script_name}】执行失败"
                )
                summary["error_type"] = "FailureRetryProcessFailed"
            if "_MatchLoopLimit" in failed_node or "_ColorMarkerLimit" in failed_node:
                loop_step = (
                    script.get("steps", [])[failed_index]
                    if isinstance(failed_index, int)
                    and 0 <= failed_index < len(script.get("steps", []))
                    else {}
                )
                summary["error"] = (
                    "动态颜色标记点击达到最大次数，页面中仍存在可收取标记"
                    if isinstance(loop_step, dict) and loop_step.get("click_mode") == "color_marker"
                    else "重复图片点击达到最大次数，页面中仍存在目标图片"
                )
                summary["error_type"] = "MatchLoopLimitExceeded"
            course_failure = next(
                (
                    item.get("result")
                    for item in reversed(collector["custom_actions"])
                    if item.get("action") == "course_schedule"
                    and isinstance(item.get("result"), dict)
                    and item["result"].get("success") is False
                ),
                None,
            )
            if isinstance(course_failure, dict):
                summary["error"] = str(course_failure.get("error") or "课程表巡回执行失败")
                summary["error_type"] = "CourseScheduleFailed"
            if (
                failure_retry_limit is None
                and isinstance(failed_index, int)
                and 0 <= failed_index < len(script.get("steps", []))
            ):
                failed_assertion = script["steps"][failed_index].get("post_assertion")
                if (
                    isinstance(failed_assertion, dict)
                    and failed_assertion.get("enabled") is True
                    and (not failed_node or "_Assert" in failed_node)
                ):
                    summary["error"] = "执行后断言失败：重试后仍未识别到目标图片"
                    summary["error_type"] = "PostAssertionFailed"
            enrich_recognition_failure(summary, recognition_tracker, budget_clock)
            summary["failure_diagnosis"] = self._diagnose_failure(script, summary)
            raise MaaExecutionError(summary["error"], summary)
        return summary

    def _ensure_controller(self) -> Any:
        if self.controller is not None:
            try:
                if self.controller.connected:
                    return self.controller
            except Exception:
                pass
            self.controller = None
            self._controller_address = ""

        adb_path = self.adb_path
        devices = self._maa["Toolkit"].find_adb_devices(adb_path)
        requested = str(getattr(self.device, "serial", "") or "").strip()
        selected = None
        if not requested:
            raise MaaExecutionError("Maa runtime requires an explicitly bound ADB serial")
        for candidate in devices:
            if candidate.address == requested or candidate.name == requested:
                selected = candidate
                break
        if selected is None:
            detail = f" for serial {requested}" if requested else ""
            self.last_error = f"MaaFramework did not find an ADB device{detail}"
            raise MaaExecutionError(self.last_error)

        method_candidates = self._unique_ints(
            self._adb_screencap_methods,
            int(selected.screencap_methods) & 7,
            2,  # Encode: adb exec-out screencap -p
            1,  # EncodeToFileAndPull
            4,  # RawWithGzip
        )
        input_methods = self._adb_input_methods or (int(selected.input_methods) & 7) or 7
        failures: list[str] = []
        for screencap_methods in method_candidates:
            controller = self._maa["AdbController"](
                selected.adb_path,
                selected.address,
                screencap_methods,
                input_methods,
                selected.config,
            )
            if self._screenshot_mode == "raw":
                controller.set_screenshot_use_raw_size(True)
            else:
                controller.set_screenshot_target_short_side(self._screenshot_short_side)
            connection = controller.post_connection().wait()
            if connection.succeeded:
                self.controller = controller
                self._controller_address = selected.address
                self.last_error = ""
                return controller
            failures.append(str(screencap_methods))

        self.last_error = (
            f"MaaFramework ADB controller failed for {selected.address}; "
            f"screencap methods tried: {', '.join(failures)}"
        )
        raise MaaExecutionError(self.last_error)

    @staticmethod
    def _unique_ints(*values: int | None) -> list[int]:
        result: list[int] = []
        for value in values:
            if value is not None and value > 0 and value not in result:
                result.append(value)
        return result

    @staticmethod
    def _failure_retry_limit(
        custom_actions: list[dict[str, Any]],
        *,
        node_prefix: str = "",
    ) -> dict[str, Any] | None:
        for item in reversed(custom_actions):
            if item.get("action") != "failure_retry_limit":
                continue
            if node_prefix and not str(item.get("node") or "").startswith(node_prefix):
                continue
            result = item.get("result")
            return result if isinstance(result, dict) else {}
        return None

    @staticmethod
    def _failure_retry_process_failure(
        custom_actions: list[dict[str, Any]],
        *,
        node_prefix: str = "",
    ) -> dict[str, Any] | None:
        for item in reversed(custom_actions):
            if item.get("action") != "failure_retry_process":
                continue
            if node_prefix and not str(item.get("node") or "").startswith(node_prefix):
                continue
            result = item.get("result")
            if isinstance(result, dict) and result.get("success") is False:
                return result
        return None

    @staticmethod
    def _require_job(job: Any, operation: str) -> None:
        job.wait()
        if not job.succeeded:
            error = f"MaaFramework failed to {operation}"
            image_load = operation == "load compiled image assets"
            raise MaaExecutionError(
                error,
                {
                    "success": False,
                    "error": error,
                    "error_type": "MaaImageResourceLoadFailed"
                    if image_load
                    else "MaaResourceLoadFailed",
                    "backend": "maafw",
                    "failure_diagnosis": {
                        "stage": "image_load" if image_load else "resource_load",
                        "title": "读取图片失败" if image_load else "加载 Maa 资源失败",
                        "message": (
                            "脚本引用的识别图片未能加载，尚未开始识别或点击。"
                            if image_load
                            else f"MaaFramework 无法完成资源加载：{operation}。"
                        ),
                    },
                },
            )

    @staticmethod
    def _json_object(value: str) -> dict[str, Any]:
        if not value:
            return {}
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("custom Maa parameter must be an object")
        return parsed

    @staticmethod
    def _first_number(texts: list[str]) -> float | None:
        for text in texts:
            match = re.search(r"[-+]?(?:\d[\d,.]*\d|\d|[.,]\d+)", text.replace(" ", ""))
            if not match:
                continue
            token = match.group(0)
            if "," in token and "." in token:
                token = token.replace(",", "")
            elif "," in token:
                groups = token.lstrip("+-").split(",")
                token = (
                    token.replace(",", "")
                    if len(groups) > 2 or len(groups[-1]) == 3
                    else token.replace(",", ".")
                )
            try:
                value = float(token)
            except ValueError:
                continue
            if math.isfinite(value):
                return value
        return None
