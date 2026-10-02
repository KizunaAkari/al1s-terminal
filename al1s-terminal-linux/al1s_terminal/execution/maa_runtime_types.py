from __future__ import annotations

# ruff: noqa: RUF001
import importlib
from typing import Any


class MaaExecutionError(RuntimeError):
    def __init__(self, message: str, execution_result: dict[str, Any] | None = None):
        super().__init__(message)
        result = execution_result or {"success": False, "error": message}
        result.setdefault("success", False)
        result.setdefault("error", message)
        result.setdefault("backend", "maafw")
        if "failure_diagnosis" not in result:
            error_type, diagnosis = self._classify_setup_failure(
                message,
                str(result.get("error_type") or ""),
            )
            result.setdefault("error_type", error_type)
            result["failure_diagnosis"] = diagnosis
        self.execution_result = result

    @staticmethod
    def _classify_setup_failure(
        message: str,
        error_type: str,
    ) -> tuple[str, dict[str, str]]:
        known = {
            "MaaFrameworkUnavailable": (
                "framework",
                "MaaFramework 运行库不可用",
                "终端未能加载 MaaFramework 运行库，脚本尚未开始执行。",
            ),
            "YoloProviderUnavailable": (
                "yolo_runtime",
                "YOLO/NPU 识别器不可用",
                "脚本需要 YOLO 识别，但 RKNN YOLO Provider 未正确初始化。",
            ),
            "MaaOcrModelUnavailable": (
                "ocr_resource",
                "OCR 模型不可用",
                "脚本需要 OCR 识别，但 MaaFramework OCR 模型不完整。",
            ),
        }
        if error_type in known:
            stage, title, detail = known[error_type]
            return error_type, {"stage": stage, "title": title, "message": detail}
        if "did not find an ADB device" in message:
            return "AdbDeviceNotFound", {
                "stage": "device_connection",
                "title": "Android 手机未连接",
                "message": (
                    "MaaFramework 未找到脚本绑定的 ADB 设备，请检查 USB、ADB 授权和设备序列号。"
                ),
            }
        if "ADB controller failed" in message:
            return "AdbControllerConnectionFailed", {
                "stage": "device_controller",
                "title": "Maa ADB 控制器连接失败",
                "message": "ADB 能发现设备，但 MaaFramework 无法建立截图或输入控制通道。",
            }
        if "Tasker initialization failed" in message:
            return "MaaTaskerInitializationFailed", {
                "stage": "tasker",
                "title": "Maa 任务执行器初始化失败",
                "message": "资源和控制器未能绑定为可执行的 Maa Tasker，脚本尚未开始。",
            }
        if "custom extension registration failed" in message:
            return "MaaExtensionRegistrationFailed", {
                "stage": "extension",
                "title": "Maa 扩展功能注册失败",
                "message": "脚本所需的自定义动作或识别器未能注册。",
            }
        if "must be an integer bitmask" in message:
            return "MaaControllerConfigurationInvalid", {
                "stage": "configuration",
                "title": "Maa 控制器配置无效",
                "message": "ADB 截图或输入方式配置不是有效的整数位掩码。",
            }
        return error_type or "MaaExecutionFailed", {
            "stage": "pipeline",
            "title": "MaaFramework 执行失败",
            "message": message,
        }


class YoloAdapter:
    """Load the board-specific YOLO implementation as a Maa custom recognizer.

    RKNN output decoding depends on the exact exported model. The provider is an
    explicit plugin instead of silently loading an x86/PyTorch ultralytics stack
    on the RK3576 terminal.
    """

    def __init__(self, provider_spec: str | None = None):
        self.provider_spec = (provider_spec or "").strip()
        self.provider: Any = None
        self.error = "MAA_YOLO_PROVIDER is not configured"
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
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
        self._load()
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
        self._load()
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
