import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from al1s_terminal.execution.maa_runtime import MaaExecutionOutcome
from al1s_terminal.lineup.session import InferenceSession

CAPABILITY = "lineup-recognition-v1"
MODEL_VERSION = "lineup-portrait-yolov8n-ppocrv5-v4"


class LineupExecutor:
    def __init__(self, assets: Path | None = None):
        self.assets = assets or Path("/opt/al1s-assets/lineup-v1")
        self.ocr_path = self.assets / "ocr"
        self._session = InferenceSession(self.assets)

    def available(self) -> bool:
        return bool(
            self.ocr_path
            and all(
                (self.assets / folder / p).is_file()
                for folder in ("ocr", "ocr-fallback")
                for p in ("det.onnx", "rec.onnx", "keys.txt")
            )
            and (self.assets / "catalog.json").is_file()
            and (self.assets / "detector.onnx").is_file()
            and (self.assets / "portrait-samples.json").is_file()
            and all(
                (self.assets / "roles" / f"{role}.png").is_file() for role in ("sword", "shield")
            )
        )

    def execute(
        self,
        manifest: dict[str, Any],
        resources: dict[str, Path],
        timeout: int,
        cancelled: Callable[[], bool],
    ) -> MaaExecutionOutcome:
        if cancelled():
            return MaaExecutionOutcome(False, "execution_cancelled", {})
        layout_hint = manifest.get("layout_hint", "auto")
        recognition_mode = manifest.get("recognition_mode", "auto")
        if layout_hint not in (
            "auto",
            "attack",
            "defense",
            "left_attack",
            "right_attack",
        ) or recognition_mode not in ("auto", "portrait", "text"):
            return MaaExecutionOutcome(False, "lineup_parameters_invalid", {})
        if not self.available():
            return MaaExecutionOutcome(False, "lineup_model_unavailable", {})
        assert self.ocr_path is not None
        catalog = json.loads((self.assets / "catalog.json").read_text(encoding="utf-8"))
        if (
            manifest.get("catalog_version") != catalog["version"]
            or manifest.get("model_version") != MODEL_VERSION
        ):
            return MaaExecutionOutcome(False, "lineup_version_mismatch", {})
        source = resources.get("input.png")
        if source is None or not source.is_file():
            return MaaExecutionOutcome(False, "lineup_input_missing", {})
        return self._session.execute(source, layout_hint, recognition_mode, timeout, cancelled)

    def close(self) -> None:
        self._session.close()
