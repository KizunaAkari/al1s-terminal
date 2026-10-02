from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class MaaProbeResult:
    available: bool
    version: str | None
    ocr_available: bool
    ocr_model_dir: str
    error_code: str | None = None
    diagnostic: str | None = None


ModuleImporter = Callable[[str], Any]


class MaaProvider:
    def __init__(
        self,
        *,
        data_dir: Path,
        ocr_model_dir: Path | None = None,
        importer: ModuleImporter = importlib.import_module,
    ) -> None:
        self._data_dir = data_dir
        self._ocr_model_dir = ocr_model_dir or data_dir / "maa" / "resource" / "model" / "ocr"
        self._importer = importer

    def probe(self) -> MaaProbeResult:
        try:
            modules = {
                name: self._importer(name)
                for name in (
                    "maa.controller",
                    "maa.custom_action",
                    "maa.custom_recognition",
                    "maa.library",
                    "maa.pipeline",
                    "maa.resource",
                    "maa.tasker",
                    "maa.toolkit",
                )
            }
            library_module = modules["maa.library"]
            version = str(library_module.Library.version())
        except (ImportError, OSError, AttributeError, RuntimeError) as exc:
            return MaaProbeResult(
                available=False,
                version=None,
                ocr_available=False,
                ocr_model_dir=str(self._ocr_model_dir),
                error_code="maa_runtime_unavailable",
                diagnostic=_bounded(str(exc)),
            )
        return MaaProbeResult(
            available=True,
            version=version,
            ocr_available=all(
                (self._ocr_model_dir / name).is_file()
                for name in ("det.onnx", "rec.onnx", "keys.txt")
            ),
            ocr_model_dir=str(self._ocr_model_dir),
        )


def _bounded(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ")[:512]
