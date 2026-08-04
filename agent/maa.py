from __future__ import annotations

import importlib
import json
import math
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .maa_pipeline import CompiledMaaTask, MaaPipelineCompiler


class MaaExecutionError(RuntimeError):
    def __init__(self, message: str, execution_result: dict[str, Any] | None = None):
        super().__init__(message)
        self.execution_result = execution_result or {"success": False, "error": message}


class YoloAdapter:
    """Load the board-specific YOLO implementation as a Maa custom recognizer.

    RKNN output decoding depends on the exact exported model. The provider is an
    explicit plugin instead of silently loading an x86/PyTorch ultralytics stack
    on the RK3576 terminal. Configure `MAA_YOLO_PROVIDER=module:ClassName`.
    """

    def __init__(self, provider_spec: str | None = None):
        self.provider_spec = (provider_spec or os.getenv("MAA_YOLO_PROVIDER", "")).strip()
        self.provider: Any = None
        self.error = "MAA_YOLO_PROVIDER is not configured"
        if not self.provider_spec:
            return
        try:
            module_name, separator, attribute = self.provider_spec.partition(":")
            if not separator or not module_name or not attribute:
                raise ValueError("provider must use module:ClassName format")
            provider_type = getattr(importlib.import_module(module_name), attribute)
            self.provider = provider_type()
            if bool(getattr(self.provider, "available", True)):
                self.error = ""
            else:
                self.error = str(getattr(self.provider, "error", "provider initialization failed"))
        except Exception as exc:
            self.error = str(exc)

    @property
    def available(self) -> bool:
        return self.provider is not None and bool(getattr(self.provider, "available", True))

    def detect(self, image: Any, params: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            raise RuntimeError(f"RKNN YOLO provider is unavailable: {self.error}")
        result = self.provider.detect(image, params)
        if not isinstance(result, dict):
            raise TypeError("YOLO provider detect() must return a dict")
        detections = result.get("detections", [])
        if not isinstance(detections, list):
            raise TypeError("YOLO provider detections must be a list")
        return result

    def status(self) -> dict[str, Any]:
        result = {
            "available": self.available,
            "provider": self.provider_spec or None,
            "error": self.error or None,
        }
        if self.provider is not None:
            provider_status = getattr(self.provider, "status", None)
            if callable(provider_status):
                result["runtime"] = provider_status()
        return result


class MaaAdapter:
    """MaaFramework runtime for the terminal's browser-authored test scripts."""

    def __init__(self, device: Any, yolo: YoloAdapter | None = None):
        self.device = device
        self.workdir = Path(getattr(device, "workdir", "./agent-data"))
        self.compiler = MaaPipelineCompiler(self.workdir)
        self.yolo = yolo or YoloAdapter()
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
        configured = os.getenv("MAA_OCR_MODEL_DIR", "").strip()
        if configured:
            return Path(configured)
        return self.workdir / "maa" / "resource" / "model" / "ocr"

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
            "screenshot_mode": os.getenv("MAA_SCREENSHOT_MODE", "raw"),
            "ocr_available": self.ocr_available,
            "ocr_model_dir": str(self.ocr_model_dir),
            "error": self.last_error or None,
        }

    def run(self, script: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
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
        compiled = self.compiler.compile(script)
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

        controller = self._ensure_controller()
        resource = self._maa["Resource"]()
        tasker = self._maa["Tasker"]()
        collector: dict[str, list[dict[str, Any]]] = {
            "custom_actions": [],
            "numeric_conditions": [],
            "yolo": [],
            "feedback": [],
        }

        if any(compiled.image_dir.iterdir()):
            self._require_job(resource.post_image(compiled.image_dir), "load compiled image assets")
        if compiled.requires_ocr:
            self._require_job(resource.post_ocr_model(self.ocr_model_dir), "load Maa OCR model")
        self._register_custom_extensions(resource, collector)

        if not tasker.bind(resource, controller) or not tasker.inited:
            raise MaaExecutionError("MaaFramework Tasker initialization failed")

        job = tasker.post_task(compiled.entry, compiled.pipeline).wait()
        detail = job.get()
        summary = self._summarize(compiled, script, detail, collector)
        summary.update({
            "backend": "maafw",
            "maa_version": self.framework_version,
            "duration_seconds": round(time.monotonic() - started, 3),
            "script_hash": compiled.script_hash,
            "active_packages": compiled.active_packages,
            "success": bool(job.succeeded),
        })
        if not job.succeeded:
            summary["error"] = "MaaFramework pipeline execution failed"
            summary["error_type"] = "MaaPipelineFailed"
            summary["failed_step"] = self._find_failed_step(compiled, detail)
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
                script_name = str(
                    failure_retry_process.get("process_script_name") or "过程脚本"
                )
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
            if "_MatchLoopLimit" in failed_node:
                summary["error"] = "重复图片点击达到最大次数，页面中仍存在目标图片"
                summary["error_type"] = "MatchLoopLimitExceeded"
            if (
                failure_retry_limit is None
                and isinstance(failed_index, int)
                and 0 <= failed_index < len(script.get("steps", []))
            ):
                failed_assertion = script["steps"][failed_index].get("post_assertion")
                if isinstance(failed_assertion, dict) and failed_assertion.get("enabled") is True and (
                    not failed_node or "_Assert" in failed_node
                ):
                    summary["error"] = "执行后断言失败：重试后仍未识别到目标图片"
                    summary["error_type"] = "PostAssertionFailed"
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

        adb_path = os.getenv("MAA_ADB_PATH", "").strip() or shutil.which("adb") or "adb"
        devices = self._maa["Toolkit"].find_adb_devices(adb_path)
        requested = str(getattr(self.device, "serial", "") or "").strip()
        selected = None
        if requested:
            for candidate in devices:
                if candidate.address == requested or candidate.name == requested:
                    selected = candidate
                    break
        elif len(devices) == 1:
            selected = devices[0]
        elif devices:
            selected = devices[0]
        if selected is None:
            detail = f" for serial {requested}" if requested else ""
            self.last_error = f"MaaFramework did not find an ADB device{detail}"
            raise MaaExecutionError(self.last_error)

        configured_methods = self._optional_int_env("MAA_ADB_SCREENCAP_METHODS")
        method_candidates = self._unique_ints(
            configured_methods,
            int(selected.screencap_methods) & 7,
            2,  # Encode: adb exec-out screencap -p
            1,  # EncodeToFileAndPull
            4,  # RawWithGzip
        )
        input_methods = (
            self._optional_int_env("MAA_ADB_INPUT_METHODS")
            or (int(selected.input_methods) & 7)
            or 7
        )
        failures: list[str] = []
        for screencap_methods in method_candidates:
            controller = self._maa["AdbController"](
                selected.adb_path,
                selected.address,
                screencap_methods,
                input_methods,
                selected.config,
            )
            screenshot_mode = os.getenv("MAA_SCREENSHOT_MODE", "raw").strip().lower()
            if screenshot_mode == "raw":
                controller.set_screenshot_use_raw_size(True)
            else:
                short_side = max(360, int(os.getenv("MAA_SCREENSHOT_SHORT_SIDE", "720")))
                controller.set_screenshot_target_short_side(short_side)
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
    def _optional_int_env(name: str) -> int | None:
        value = os.getenv(name, "").strip()
        if not value:
            return None
        try:
            result = int(value, 0)
        except ValueError as exc:
            raise MaaExecutionError(f"{name} must be an integer bitmask") from exc
        return result if result > 0 else None

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
            raise MaaExecutionError(f"MaaFramework failed to {operation}")

    def _register_custom_extensions(
        self,
        resource: Any,
        collector: dict[str, list[dict[str, Any]]],
    ) -> None:
        CustomAction = self._maa["CustomAction"]
        CustomRecognition = self._maa["CustomRecognition"]
        JOCR = self._maa["JOCR"]
        JRecognitionType = self._maa["JRecognitionType"]
        JTemplateMatch = self._maa["JTemplateMatch"]
        device = self.device
        yolo = self.yolo

        class StartSessionAction(CustomAction):
            def run(self, _context: Any, argv: Any) -> bool:
                result = device.start_session()
                collector["custom_actions"].append({
                    "node": argv.node_name,
                    "action": "start",
                    "result": result,
                })
                return True

        class FeedbackAction(CustomAction):
            def run(self, _context: Any, argv: Any) -> bool:
                config = MaaAdapter._json_object(argv.custom_action_param)
                feedback = {
                    "node": argv.node_name,
                    "subject": str(config.get("subject") or ""),
                    "message": str(config.get("message") or ""),
                    "capture": device.screenshot(),
                }
                collector["feedback"].append(feedback)
                return True

        class ScreenshotAction(CustomAction):
            def run(self, _context: Any, argv: Any) -> bool:
                collector["custom_actions"].append({
                    "node": argv.node_name,
                    "action": "screenshot",
                    "result": device.screenshot(),
                })
                return True

        class MatchLoopLimitAction(CustomAction):
            def run(self, _context: Any, argv: Any) -> bool:
                config = MaaAdapter._json_object(argv.custom_action_param)
                collector["custom_actions"].append({
                    "node": argv.node_name,
                    "action": "match_loop_limit",
                    "result": config,
                })
                return False

        class FailureRetryLimitAction(CustomAction):
            def run(self, _context: Any, argv: Any) -> bool:
                config = MaaAdapter._json_object(argv.custom_action_param)
                collector["custom_actions"].append({
                    "node": argv.node_name,
                    "action": "failure_retry_limit",
                    "result": config,
                })
                return False

        class FailureRetryProcessAction(CustomAction):
            def run(self, context: Any, argv: Any) -> bool:
                config = MaaAdapter._json_object(argv.custom_action_param)
                result: dict[str, Any] = {
                    "target_step_index": config.get("target_step_index"),
                    "process_script_name": str(
                        config.get("process_script_name") or ""
                    ),
                    "success": False,
                }
                try:
                    entry = str(config.get("entry") or "")
                    pipeline = config.get("pipeline")
                    if not entry or not isinstance(pipeline, dict):
                        raise ValueError("failure retry process pipeline is incomplete")
                    detail = context.run_task(entry, pipeline)
                    result["success"] = bool(
                        detail is not None and detail.status.succeeded
                    )
                    if detail is not None:
                        result["task_id"] = detail.task_id
                        result["nodes"] = [
                            {
                                "name": node.name,
                                "completed": bool(node.completed),
                            }
                            for node in detail.nodes
                        ]
                except Exception as exc:
                    result["error"] = str(exc)
                collector["custom_actions"].append({
                    "node": argv.node_name,
                    "action": "failure_retry_process",
                    "result": result,
                })
                return bool(result["success"])

        class NumericCompareRecognition(CustomRecognition):
            def analyze(self, context: Any, argv: Any) -> Any:
                config = MaaAdapter._json_object(argv.custom_recognition_param)
                region = config.get("region", [argv.roi.x, argv.roi.y, argv.roi.w, argv.roi.h])
                attempt: dict[str, Any] = {
                    "node": argv.node_name,
                    "mode": "numeric",
                    "operator": config.get("operator"),
                    "threshold": config.get("value"),
                    "region": region,
                    "hit": False,
                }
                try:
                    recognition = context.run_recognition_direct(
                        JRecognitionType.OCR,
                        JOCR(expected=[], roi=tuple(int(value) for value in region)),
                        argv.image,
                    )
                    texts = []
                    if recognition is not None:
                        texts = [
                            str(getattr(item, "text", ""))
                            for item in recognition.all_results
                            if str(getattr(item, "text", ""))
                        ]
                    number = MaaAdapter._first_number(texts)
                    attempt.update({"texts": texts, "recognized_value": number})
                    if number is None:
                        attempt["fallback"] = "execute_step"
                        collector["numeric_conditions"].append(attempt)
                        return None
                    threshold = float(config["value"])
                    operator = str(config["operator"])
                    hit = number > threshold if operator == "gt" else number < threshold
                    attempt["hit"] = hit
                    collector["numeric_conditions"].append(attempt)
                    if not hit:
                        return None
                    return CustomRecognition.AnalyzeResult(
                        box=tuple(int(value) for value in region),
                        detail=attempt,
                    )
                except Exception as exc:
                    attempt.update({"error": str(exc), "fallback": "execute_step"})
                    collector["numeric_conditions"].append(attempt)
                    return None

        class MatchOffsetRecognition(CustomRecognition):
            def analyze(self, context: Any, argv: Any) -> Any:
                config = MaaAdapter._json_object(argv.custom_recognition_param)
                preferred_index = max(0, int(config.get("preferred_index", 0)))
                region = (
                    int(argv.roi.x),
                    int(argv.roi.y),
                    int(argv.roi.w),
                    int(argv.roi.h),
                )
                recognition = context.run_recognition_direct(
                    JRecognitionType.TemplateMatch,
                    JTemplateMatch(
                        template=[str(config["template"])],
                        roi=region,
                        threshold=[float(config.get("threshold", 0.85))],
                        order_by=str(config.get("order_by") or "Vertical"),
                        index=0,
                    ),
                    argv.image,
                )
                if recognition is None or not recognition.hit:
                    return None
                results = recognition.filtered_results or recognition.all_results
                if not results:
                    return None
                selected_index = min(preferred_index, len(results) - 1)
                selected = results[selected_index]
                return CustomRecognition.AnalyzeResult(
                    box=MaaAdapter._rect_tuple(selected.box),
                    detail={
                        "matches": len(results),
                        "preferred_index": preferred_index,
                        "selected_index": selected_index,
                        "score": float(selected.score),
                    },
                )

        class YoloRecognition(CustomRecognition):
            def analyze(self, _context: Any, argv: Any) -> Any:
                config = MaaAdapter._json_object(argv.custom_recognition_param)
                image = argv.image
                offset_x = 0
                offset_y = 0
                if argv.roi.w > 0 and argv.roi.h > 0:
                    offset_x, offset_y = int(argv.roi.x), int(argv.roi.y)
                    image = image[
                        offset_y:offset_y + int(argv.roi.h),
                        offset_x:offset_x + int(argv.roi.w),
                    ]
                result = yolo.detect(image, config)
                if offset_x or offset_y:
                    for detection in result.get("detections", []):
                        box = detection.get("box") or detection.get("xywh")
                        if isinstance(box, (list, tuple)) and len(box) == 4:
                            detection["box"] = [box[0] + offset_x, box[1] + offset_y, box[2], box[3]]
                        xyxy = detection.get("xyxy")
                        if isinstance(xyxy, (list, tuple)) and len(xyxy) == 4:
                            detection["xyxy"] = [
                                xyxy[0] + offset_x,
                                xyxy[1] + offset_y,
                                xyxy[2] + offset_x,
                                xyxy[3] + offset_y,
                            ]
                collector["yolo"].append({"node": argv.node_name, **result})
                detections = result.get("detections", [])
                if not detections:
                    return None
                selected = detections[0]
                box = selected.get("box") or selected.get("xywh")
                if box is None and selected.get("xyxy") is not None:
                    x1, y1, x2, y2 = selected["xyxy"]
                    box = [x1, y1, x2 - x1, y2 - y1]
                if not isinstance(box, (list, tuple)) or len(box) != 4:
                    raise ValueError("YOLO detection must provide box/xywh/xyxy")
                return CustomRecognition.AnalyzeResult(
                    box=tuple(int(round(value)) for value in box),
                    detail=result,
                )

        registrations = (
            resource.register_custom_action(MaaPipelineCompiler.START_ACTION, StartSessionAction()),
            resource.register_custom_action(MaaPipelineCompiler.FEEDBACK_ACTION, FeedbackAction()),
            resource.register_custom_action(MaaPipelineCompiler.SCREENSHOT_ACTION, ScreenshotAction()),
            resource.register_custom_action(
                MaaPipelineCompiler.MATCH_LOOP_LIMIT_ACTION,
                MatchLoopLimitAction(),
            ),
            resource.register_custom_action(
                MaaPipelineCompiler.FAILURE_RETRY_PROCESS_ACTION,
                FailureRetryProcessAction(),
            ),
            resource.register_custom_action(
                MaaPipelineCompiler.FAILURE_RETRY_LIMIT_ACTION,
                FailureRetryLimitAction(),
            ),
            resource.register_custom_recognition(
                MaaPipelineCompiler.NUMERIC_RECOGNITION,
                NumericCompareRecognition(),
            ),
            resource.register_custom_recognition(
                MaaPipelineCompiler.MATCH_OFFSET_RECOGNITION,
                MatchOffsetRecognition(),
            ),
            resource.register_custom_recognition(
                MaaPipelineCompiler.YOLO_RECOGNITION,
                YoloRecognition(),
            ),
        )
        if not all(registrations):
            raise MaaExecutionError("MaaFramework custom extension registration failed")

    def _summarize(
        self,
        compiled: CompiledMaaTask,
        script: dict[str, Any],
        detail: Any,
        collector: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        node_records: list[dict[str, Any]] = []
        if detail is not None:
            for node in detail.nodes:
                record: dict[str, Any] = {
                    "node_id": node.node_id,
                    "name": node.name,
                    "completed": bool(node.completed),
                }
                if node.name in compiled.node_steps:
                    record["step_index"] = compiled.node_steps[node.name]
                if node.recognition is not None:
                    best = node.recognition.best_result
                    record["recognition"] = {
                        "algorithm": str(node.recognition.algorithm),
                        "hit": bool(node.recognition.hit),
                        "box": self._rect_dict(node.recognition.box),
                        "score": getattr(best, "score", None) if best is not None else None,
                    }
                if node.action is not None:
                    record["action"] = {
                        "type": str(node.action.action),
                        "success": bool(node.action.success),
                    }
                node_records.append(record)

        steps: list[dict[str, Any]] = []
        conditional_skips: list[dict[str, Any]] = []
        script_steps = script.get("steps", [])
        for index, step in enumerate(script_steps):
            matching = [record for record in node_records if record.get("step_index") == index]
            skipped = any("_SkipIf" in record["name"] and record["completed"] for record in matching)
            result: dict[str, Any] = {"backend": "maafw", "nodes": matching}
            feedback = [item for item in collector["feedback"] if item["node"] in compiled.step_nodes.get(index, [])]
            if feedback:
                result["feedback"] = feedback[-1]
            custom_actions = [
                item for item in collector["custom_actions"]
                if item["node"] in compiled.step_nodes.get(index, [])
            ]
            if custom_actions:
                result["custom_actions"] = custom_actions
            loop_clicks = [
                item for item in matching
                if (
                    "_MatchLoopClick_" in item["name"]
                    or "_MatchLoopFallback_" in item["name"]
                )
                and item["completed"]
            ]
            if loop_clicks or step.get("click_mode") == "match_offset":
                result["match_loop"] = {
                    "clicks": len(loop_clicks),
                    "completed": bool(
                        any("_MatchLoopDone" in item["name"] and item["completed"] for item in matching)
                    ),
                    "max_clicks": int(step.get("match_max_clicks", 50)),
                }
            conditions = [
                item for item in collector["numeric_conditions"]
                if item["node"] in compiled.step_nodes.get(index, [])
            ]
            record: dict[str, Any] = {
                "index": index,
                "action": step.get("action"),
                "result": result,
            }
            if skipped:
                record["skipped"] = True
                result["reason"] = "numeric_condition"
            if conditions:
                record["condition"] = conditions[-1]
            condition_config = step.get("skip_condition")
            if (
                isinstance(condition_config, dict)
                and condition_config.get("enabled") is True
                and condition_config.get("mode") == "image"
            ):
                guard_records = [
                    item for item in matching
                    if "_SkipIfImage" in item["name"]
                ]
                recognition = (
                    guard_records[-1].get("recognition")
                    if guard_records
                    else None
                )
                record["condition"] = {
                    "mode": "image",
                    "threshold": float(condition_config.get("threshold", 0.85)),
                    "hit": skipped,
                    "score": (
                        recognition.get("score")
                        if isinstance(recognition, dict)
                        else None
                    ),
                }
            if skipped and isinstance(condition_config, dict) and condition_config.get("enabled") is True:
                skip_remaining = condition_config.get("skip_remaining_steps") is True
                skipped_indexes = (
                    list(range(index, len(script_steps)))
                    if skip_remaining
                    else [index]
                )
                conditional_skips.append({
                    "trigger_step_index": index,
                    "trigger_step_number": index + 1,
                    "trigger_action": step.get("action"),
                    "mode": str(condition_config.get("mode") or "numeric"),
                    "operator": condition_config.get("operator"),
                    "value": condition_config.get("value"),
                    "threshold": condition_config.get("threshold"),
                    "scope": "remaining" if skip_remaining else "current",
                    "skipped_step_indexes": skipped_indexes,
                    "skipped_step_numbers": [item + 1 for item in skipped_indexes],
                })
            assertion = step.get("post_assertion")
            if isinstance(assertion, dict) and assertion.get("enabled") is True:
                assertion_hits = [
                    item for item in matching
                    if "_Assert" in item["name"]
                    and "_AssertRetry" not in item["name"]
                    and item["completed"]
                ]
                retries = sum(
                    1 for item in matching
                    if "_AssertRetry" in item["name"] and item["completed"]
                )
                result["post_assertion"] = {
                    "enabled": True,
                    "succeeded": bool(assertion_hits),
                    "retries": retries,
                    "max_retries": int(assertion.get("max_retries", 2)),
                }
            failure_retry = step.get("failure_retry")
            if isinstance(failure_retry, dict) and failure_retry.get("enabled") is True:
                process_runs = [
                    item.get("result")
                    for item in collector["custom_actions"]
                    if (
                        item.get("action") == "failure_retry_process"
                        and str(item.get("node") or "").startswith(
                            f"Web_{compiled.script_hash}_"
                        )
                        and isinstance(item.get("result"), dict)
                        and item["result"].get("target_step_index") == index
                    )
                ]
                result["failure_retry"] = {
                    "enabled": True,
                    "process_script_name": str(
                        failure_retry.get("process_script_name") or ""
                    ),
                    "attempts": len(process_runs),
                    "max_retries": int(failure_retry.get("max_retries", 2)),
                    "executed": bool(process_runs),
                    "runs": process_runs,
                }
            if step.get("_module_name") is not None:
                record["module"] = {
                    "index": int(step.get("_module_index", 0)),
                    "name": str(step.get("_module_name")),
                    "step_index": int(step.get("_module_step_index", 0)),
                    "interval": bool(step.get("_composition_interval", False)),
                }
            steps.append(record)

        for event in conditional_skips:
            if event.get("scope") != "remaining":
                continue
            trigger_index = int(event["trigger_step_index"])
            for skipped_index in range(trigger_index + 1, len(steps)):
                skipped_step = steps[skipped_index]
                skipped_step["skipped"] = True
                skipped_step["skipped_by_step_index"] = trigger_index
                skipped_step.setdefault("result", {})["reason"] = "conditional_skip"

        global_dismissals = [
            {
                "name": record["name"],
                "backend": "maafw",
                "completed": record["completed"],
            }
            for record in node_records
            if "_Global_" in record["name"] and record["completed"]
        ]
        return {
            "steps": steps,
            "maa_nodes": node_records,
            "global_popup_dismissals": global_dismissals,
            "conditional_skips": conditional_skips,
            "numeric_conditions": collector["numeric_conditions"],
            "yolo_detections": collector["yolo"],
            "custom_actions": collector["custom_actions"],
        }

    @staticmethod
    def _find_failed_step(compiled: CompiledMaaTask, detail: Any) -> dict[str, Any]:
        completed_nodes: set[str] = set()
        if detail is not None:
            for node in detail.nodes:
                step_index = compiled.node_steps.get(node.name)
                if node.completed:
                    completed_nodes.add(node.name)
                elif step_index is not None:
                    return {"index": step_index, "number": step_index + 1, "node": node.name}
        for step_index in sorted(compiled.step_exits):
            if not any(node in completed_nodes for node in compiled.step_exits[step_index]):
                return {"index": step_index, "number": step_index + 1}
        return {"index": None, "number": None}

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
                token = token.replace(",", "") if len(groups) > 2 or len(groups[-1]) == 3 else token.replace(",", ".")
            try:
                value = float(token)
            except ValueError:
                continue
            if math.isfinite(value):
                return value
        return None

    @staticmethod
    def _rect_dict(rect: Any) -> dict[str, int] | None:
        if rect is None:
            return None
        x, y, width, height = MaaAdapter._rect_tuple(rect)
        return {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }

    @staticmethod
    def _rect_tuple(rect: Any) -> tuple[int, int, int, int]:
        if isinstance(rect, (list, tuple)) and len(rect) == 4:
            return tuple(int(value) for value in rect)
        return int(rect.x), int(rect.y), int(rect.w), int(rect.h)
