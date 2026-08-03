from __future__ import annotations

import json
from typing import Any

from .device import AndroidDevice
from .maa import MaaAdapter, YoloAdapter


SCRIPT_TYPES = {"standard", "module_start", "module_process", "composition"}


class ScriptExecutor:
    """Validate editor scripts and execute them exclusively through MaaFramework.

    MaaFramework owns recognition, polling, actions and retries. This wrapper
    keeps product-level policy around target locking, failure evidence and final
    Android application cleanup.
    """

    def __init__(self, device: AndroidDevice):
        self.device = device
        self.yolo = YoloAdapter()
        self.maa = MaaAdapter(device, self.yolo)
        self._active_packages: set[str] = set()

    def run(
        self,
        content: str | None,
        params: dict[str, Any],
        *,
        execution_mode: str | None = None,
        task_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        script = self._parse(content)
        if execution_mode:
            script["execution_mode"] = execution_mode
        elif script.get("execution_mode") == "quick_test":
            # Quick-test policy can only be enabled by the dedicated terminal
            # command, never by content submitted as a formal task.
            script["execution_mode"] = "full_script"
        script_type, execution_mode = self._validate(script)
        return self._run_maa(
            script,
            params,
            script_type=script_type,
            execution_mode=execution_mode,
            task_context=task_context or {},
        )

    @staticmethod
    def _parse(content: str | None) -> dict[str, Any]:
        if not content:
            return {"version": 1, "steps": []}
        try:
            value = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"脚本必须是 JSON DSL：{exc}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("steps", []), list):
            raise ValueError("脚本格式应为 {version: 2, steps: []}")
        return value

    def _validate(self, script: dict[str, Any]) -> tuple[str, str]:
        steps = script.get("steps", [])
        if any(not isinstance(step, dict) for step in steps):
            raise ValueError("脚本中的每个步骤都必须是对象")

        script_type = str(script.get("script_type") or "standard")
        if script_type not in SCRIPT_TYPES:
            raise ValueError(f"不支持的脚本类型: {script_type}")

        global_popups = script.get("global_popups", [])
        if not isinstance(global_popups, list):
            raise ValueError("global_popups 必须是数组")

        execution_mode = str(script.get("execution_mode") or "full_script")
        resumed_composition = (
            script_type == "composition"
            and isinstance(script.get("composition_resume"), dict)
        )
        relaxed_structure = (
            execution_mode in {"single_step", "quick_test"}
            or resumed_composition
        )
        if int(script.get("version", 1)) >= 2:
            self._validate_v2_steps(steps, script_type, relaxed_structure)
            self._validate_target(script.get("target", {}))
        return script_type, execution_mode

    @staticmethod
    def _validate_v2_steps(
        steps: list[dict[str, Any]],
        script_type: str,
        relaxed_structure: bool,
    ) -> None:
        requires_start = script_type in {"standard", "module_start", "composition"}
        if not relaxed_structure and requires_start and (
            not steps or steps[0].get("action") != "start"
        ):
            raise ValueError("版本 2 脚本必须以【开始】步骤开头")

        actions = [str(step.get("action") or "") for step in steps]
        if relaxed_structure:
            return
        if script_type == "module_start":
            if actions.count("start") != 1 or "launch_app" not in actions:
                raise ValueError(
                    "开始脚本必须包含一个【开始】步骤和至少一个【打开应用】步骤"
                )
        if script_type == "module_process" and any(
            action in {"start", "launch_app"} for action in actions
        ):
            raise ValueError("过程脚本不能包含【开始】或【打开应用】步骤")

    def _validate_target(self, target: Any) -> None:
        if target is None:
            return
        if not isinstance(target, dict):
            raise ValueError("target 必须是对象")
        expected_serial = str(target.get("device_serial", "")).strip()
        if not expected_serial or expected_serial == "default":
            return
        state = self.device.state()
        if not state.get("connected"):
            raise RuntimeError("目标手机未连接")
        if state.get("serial") != expected_serial:
            raise RuntimeError(
                f"目标手机不匹配：期望 {expected_serial}，当前 {state.get('serial')}"
            )

    def _run_maa(
        self,
        script: dict[str, Any],
        params: dict[str, Any],
        *,
        script_type: str,
        execution_mode: str,
        task_context: dict[str, Any],
    ) -> dict[str, Any]:
        preview_mode = execution_mode in {"single_step", "quick_test"}
        composition_retry_pending = self._composition_retry_pending(
            script_type,
            task_context,
        )
        self._active_packages = self._collect_active_packages(script, script_type)
        cleanup_on_success = (
            script_type not in {"module_start", "module_process"}
            or bool(script.get("cleanup_on_finish", False))
        )

        try:
            result = self.maa.run(script, params)
        except Exception as exc:
            failure = getattr(exc, "execution_result", None)
            if not isinstance(failure, dict):
                failure = {
                    "success": False,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "backend": "maafw",
                }
            self._fill_failed_step_action(failure, script)
            composition_resume_available = self._composition_resume_available(failure, script)
            phone_state_preserved = composition_retry_pending and composition_resume_available
            if execution_mode != "quick_test":
                self._capture_failure_screen(failure)
            cleanup = (
                self._skipped_cleanup(
                    "single_step_preview" if execution_mode == "single_step" else "quick_test_preview"
                )
                if preview_mode
                else (
                    self._skipped_cleanup("composition_retry_pending")
                    if phone_state_preserved
                    else self._cleanup_resources()
                )
            )
            failure["cleanup"] = cleanup
            if script_type == "composition":
                failure["composition_retry"] = {
                    "attempt": int(task_context.get("attempt") or 1),
                    "max_retries": int(task_context.get("max_retries") or 0),
                    "retry_pending": composition_retry_pending,
                    "resume_available": composition_resume_available,
                    "phone_state_preserved": phone_state_preserved,
                }
            failure.setdefault("script_version", script.get("version", 1))
            failure.setdefault("script_type", script_type)
            failure.setdefault(
                "execution_mode",
                execution_mode,
            )
            if cleanup["errors"]:
                failure["cleanup_error"] = "；".join(cleanup["errors"])
            setattr(exc, "execution_result", failure)
            raise
        else:
            if preview_mode:
                cleanup = self._skipped_cleanup(
                    "single_step_preview" if execution_mode == "single_step" else "quick_test_preview"
                )
            elif cleanup_on_success:
                cleanup = self._cleanup_resources()
            else:
                cleanup = self._skipped_cleanup("module_continues")

            if cleanup["errors"]:
                raise RuntimeError(
                    f"MaaFramework 任务成功，但资源清理失败："
                    f"{'；'.join(cleanup['errors'])}"
                )
            return {
                **result,
                "cleanup": cleanup,
                "script_version": script.get("version", 1),
                "script_type": script_type,
                "composition": script.get("composition", []),
                "execution_mode": execution_mode,
            }
        finally:
            self._active_packages = set()

    def _collect_active_packages(
        self,
        script: dict[str, Any],
        script_type: str,
    ) -> set[str]:
        packages = {
            str(step.get("package") or "").strip()
            for step in script.get("steps", [])
            if step.get("action") == "launch_app" and step.get("package")
        }
        if script_type == "module_process":
            try:
                foreground = self.device.detect_foreground_app()
                if foreground.get("detected") and foreground.get("package"):
                    packages.add(str(foreground["package"]))
            except Exception:
                pass
        return packages

    @staticmethod
    def _composition_retry_pending(
        script_type: str,
        task_context: dict[str, Any],
    ) -> bool:
        if script_type != "composition" or task_context.get("task_kind") != "composition":
            return False
        try:
            attempt = int(task_context.get("attempt") or 1)
            max_retries = int(task_context.get("max_retries") or 0)
        except (TypeError, ValueError):
            return False
        return max_retries > 0 and attempt <= max_retries

    @staticmethod
    def _composition_resume_available(
        failure: dict[str, Any],
        script: dict[str, Any],
    ) -> bool:
        failed_step = failure.get("failed_step")
        failed_module = failed_step.get("module") if isinstance(failed_step, dict) else None
        if not isinstance(failed_module, dict):
            return False
        try:
            resume_module_index = int(failed_module.get("index"))
        except (TypeError, ValueError):
            return False
        if bool(failed_module.get("interval", False)):
            resume_module_index += 1
        for step in script.get("steps", []):
            if not isinstance(step, dict):
                continue
            try:
                if int(step.get("_module_index")) >= resume_module_index:
                    return True
            except (TypeError, ValueError):
                continue
        return False

    @staticmethod
    def _fill_failed_step_action(
        failure: dict[str, Any],
        script: dict[str, Any],
    ) -> None:
        failed_step = failure.get("failed_step")
        if not isinstance(failed_step, dict) or failed_step.get("index") is None:
            return
        index = int(failed_step["index"])
        steps = script.get("steps", [])
        if 0 <= index < len(steps):
            step = steps[index]
            failed_step.setdefault("action", step.get("action"))
            if step.get("_module_name") is not None:
                failed_step.setdefault("module", {
                    "index": int(step.get("_module_index", 0)),
                    "name": str(step.get("_module_name")),
                    "step_index": int(step.get("_module_step_index", 0)),
                    "interval": bool(step.get("_composition_interval", False)),
                })

    def _capture_failure_screen(self, failure: dict[str, Any]) -> None:
        try:
            failure["failure_screenshot"] = self.device.screenshot()
        except Exception as exc:
            failure["failure_screenshot_error"] = str(exc)

    @staticmethod
    def _skipped_cleanup(reason: str) -> dict[str, Any]:
        return {
            "skipped": True,
            "reason": reason,
            "force_stopped_packages": [],
            "home": False,
            "errors": [],
        }

    def _cleanup_resources(self) -> dict[str, Any]:
        force_stopped_packages: list[str] = []
        errors: list[str] = []
        try:
            foreground = self.device.detect_foreground_app()
            if foreground.get("detected") and foreground.get("package"):
                self._active_packages.add(str(foreground["package"]))
        except Exception:
            # Known launched packages can still be stopped and Home can still
            # be sent when foreground detection is unavailable.
            pass

        for package in sorted(self._active_packages):
            try:
                self.device.close_app(package)
                force_stopped_packages.append(package)
            except Exception as exc:
                errors.append(f"停止 {package} 失败：{exc}")

        home = False
        try:
            self.device.home()
            home = True
        except Exception as exc:
            errors.append(f"返回桌面失败：{exc}")
        return {
            "force_stopped_packages": force_stopped_packages,
            "home": home,
            "errors": errors,
        }
