from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from al1s_terminal.execution.maa_smoke import MaaReadinessResult
from al1s_terminal.providers.maa import MaaProvider
from al1s_terminal.providers.system import include_maa_capability, probe_system


def _maa_module(name: str) -> object:
    if name == "maa.library":
        return SimpleNamespace(Library=SimpleNamespace(version=lambda: "5.12.1"))
    return SimpleNamespace()


def test_maa_probe_reports_runtime_and_ocr_assets(tmp_path: Path) -> None:
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    for name in ("det.onnx", "rec.onnx", "keys.txt"):
        (ocr / name).write_bytes(b"asset")

    provider = MaaProvider(
        data_dir=tmp_path,
        ocr_model_dir=ocr,
        importer=_maa_module,
    )

    result = provider.probe()

    assert result.available is True
    assert result.version == "5.12.1"
    assert result.ocr_available is True


def test_maa_probe_does_not_claim_missing_runtime(tmp_path: Path) -> None:
    def missing(_name: str) -> object:
        raise ImportError("not installed")

    result = MaaProvider(data_dir=tmp_path, importer=missing).probe()

    assert result.available is False
    assert result.error_code == "maa_runtime_unavailable"


def test_maa_probe_does_not_advertise_executor_before_runtime_is_ready(tmp_path: Path) -> None:
    probe = MaaProvider(
        data_dir=tmp_path,
        importer=_maa_module,
    ).probe()

    capability = include_maa_capability(probe_system(tmp_path), probe)

    assert "maa" not in capability.provider_keys
    assert capability.details["maa"]["available"] is True
    assert capability.details["maa"]["execution_ready"] is False


def test_maa_capability_exposes_real_device_smoke_result(tmp_path: Path) -> None:
    probe = MaaProvider(data_dir=tmp_path, importer=_maa_module).probe()
    readiness = MaaReadinessResult(
        ready=False,
        device_serial="phone-1",
        error_code="maa_adb_connection_failed",
        diagnostic="cannot connect",
    )

    capability = include_maa_capability(
        probe_system(tmp_path),
        probe,
        readiness=readiness,
    )

    assert "maa" not in capability.provider_keys
    assert capability.details["maa"]["smoke_device_serial"] == "phone-1"
    assert capability.details["maa"]["smoke_error_code"] == "maa_adb_connection_failed"
