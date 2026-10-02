from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from al1s_terminal.execution.maa_adapter import YoloAdapter
from al1s_terminal.providers.rknn_smoke import (
    RknnReadinessResult,
    RknnRuntimeReadinessProbe,
)
from al1s_terminal.providers.rknn_smoke import main as rknn_smoke_main
from al1s_terminal.providers.rknn_yolo import RknnYoloConfig, RknnYoloProvider
from al1s_terminal.providers.system import include_rknn_capability, probe_system
from al1s_terminal.providers.yolo_v8 import YoloV8PostProcessor


def _processor() -> YoloV8PostProcessor:
    return YoloV8PostProcessor(input_width=640, input_height=640)


def test_model_zoo_six_output_layout_is_decoded_without_torch() -> None:
    outputs: list[np.ndarray] = []
    for size in (80, 40, 20):
        outputs.extend(
            (
                np.zeros((1, 64, size, size), dtype=np.float32),
                np.zeros((1, 2, size, size), dtype=np.float32),
            )
        )
    outputs[1][0, 1, 40, 40] = 0.9

    boxes, classes, scores = _processor().decode(
        outputs,
        confidence=0.25,
        nms_threshold=0.45,
    )

    assert boxes.shape == (1, 4)
    assert classes.tolist() == [1]
    assert float(scores[0]) == pytest.approx(0.9, abs=1e-5)


def test_model_zoo_nine_output_layout_ignores_score_sum_tensor() -> None:
    outputs: list[np.ndarray] = []
    for size in (80, 40, 20):
        outputs.extend(
            (
                np.zeros((1, 64, size, size), dtype=np.float32),
                np.zeros((1, 1, size, size), dtype=np.float32),
                np.zeros((1, 1, size, size), dtype=np.float32),
            )
        )
    outputs[1][0, 0, 20, 20] = 0.8

    boxes, classes, scores = _processor().decode(
        outputs,
        confidence=0.25,
        nms_threshold=0.45,
    )

    assert len(boxes) == 1
    assert classes.tolist() == [0]
    assert float(scores[0]) == pytest.approx(0.8, abs=1e-5)


def test_flat_and_transposed_outputs_are_decoded() -> None:
    channel_first = np.zeros((1, 6, 10), dtype=np.float32)
    channel_first[0, :4, 0] = [320, 320, 100, 200]
    channel_first[0, 5, 0] = 0.9
    candidate_first = channel_first.transpose(0, 2, 1)

    first = _processor().decode([channel_first], confidence=0.25, nms_threshold=0.45)
    second = _processor().decode([candidate_first], confidence=0.25, nms_threshold=0.45)

    assert first[0].round().astype(int).tolist() == [[270, 220, 370, 420]]
    assert second[0].round().astype(int).tolist() == [[270, 220, 370, 420]]
    assert first[1].tolist() == second[1].tolist() == [1]


def test_tensor_layout_restore_and_runtime_batch_are_stable() -> None:
    position = np.zeros((1, 80, 80, 64), dtype=np.float32)
    classes = np.zeros((1, 80, 80, 3), dtype=np.float32)
    boxes = np.array([[100.0, 160.0, 540.0, 480.0]], dtype=np.float32)

    normalized_position = _processor().position_nchw(position)
    normalized_classes = _processor().class_nchw(classes, (80, 80))
    restored = _processor().restore_boxes(
        boxes,
        source_shape=(360, 720),
        ratio=640 / 720,
        padding=(0.0, 160.0),
    )
    runtime_input = RknnYoloProvider.runtime_input(np.zeros((640, 640, 3), dtype=np.uint8))

    assert normalized_position.shape == (1, 64, 80, 80)
    assert normalized_classes.shape == (1, 3, 80, 80)
    assert restored[0].round().astype(int).tolist() == [112, 0, 608, 360]
    assert runtime_input.shape == (1, 640, 640, 3)
    assert runtime_input.flags.c_contiguous


class _Runtime:
    def __init__(self) -> None:
        self.loaded: str | None = None
        self.initialized = False
        self.released = False

    def load_rknn(self, model: str) -> int:
        self.loaded = model
        return 0

    def init_runtime(self) -> int:
        self.initialized = True
        return 0

    def release(self) -> None:
        self.released = True


def test_provider_initializes_and_releases_injected_runtime(tmp_path: Path) -> None:
    model = tmp_path / "model.rknn"
    model.write_bytes(b"rknn")
    runtime = _Runtime()
    config = RknnYoloConfig(model, 640, 640, ("target",), False)

    provider = RknnYoloProvider(
        config=config,
        runtime_factory=lambda: runtime,
        cv2_module=SimpleNamespace(),
    )

    assert provider.available is True
    assert runtime.loaded == str(model)
    assert runtime.initialized is True
    provider.close()
    assert runtime.released is True


def test_provider_reports_missing_model_without_loading_runtime(tmp_path: Path) -> None:
    created = False

    def factory() -> Any:
        nonlocal created
        created = True
        return _Runtime()

    provider = RknnYoloProvider(
        config=RknnYoloConfig(tmp_path / "missing.rknn", 640, 640, ("target",), False),
        runtime_factory=factory,
        cv2_module=SimpleNamespace(),
    )

    assert provider.available is False
    assert "does not exist" in provider.error
    assert created is False


def test_yolo_adapter_does_not_load_provider_until_used(monkeypatch: Any) -> None:
    created = 0

    class Provider:
        available = True

        def __init__(self) -> None:
            nonlocal created
            created += 1

    monkeypatch.setattr(
        "al1s_terminal.execution.maa_runtime_types.importlib.import_module",
        lambda _name: SimpleNamespace(Provider=Provider),
    )
    adapter = YoloAdapter("example:Provider")

    assert created == 0
    assert adapter.available is True
    assert created == 1
    assert adapter.available is True
    assert created == 1


class _SmokeProvider:
    available = True
    error = ""

    def __init__(self) -> None:
        self.closed = False

    def status(self) -> dict[str, Any]:
        return {
            "model": "/models/test.rknn",
            "runtime_version": "2.3.2",
            "input_size": [640, 640],
        }

    def detect(self, image: Any, params: dict[str, Any]) -> dict[str, Any]:
        assert np.asarray(image).shape == (640, 640, 3)
        assert params["confidence"] == 1.0
        return {"inference_ms": 12.5, "output_shapes": [[1, 84, 8400]]}

    def close(self) -> None:
        self.closed = True


def test_rknn_capability_requires_successful_inference_and_caches_success(tmp_path: Path) -> None:
    providers: list[_SmokeProvider] = []

    def factory() -> _SmokeProvider:
        provider = _SmokeProvider()
        providers.append(provider)
        return provider

    probe = RknnRuntimeReadinessProbe(factory)
    first = probe.probe()
    second = probe.probe()
    capability = include_rknn_capability(probe_system(tmp_path), first)

    assert first.ready is True
    assert first.cached is False
    assert second.cached is True
    assert len(providers) == 1
    assert providers[0].closed is True
    assert "yolo" in capability.provider_keys
    assert capability.details["rknn_yolo"]["runtime_version"] == "2.3.2"


@pytest.mark.parametrize(("ready", "expected_exit"), [(True, 0), (False, 1)])
def test_rknn_smoke_cli_reports_json_and_exit_status(
    ready: bool,
    expected_exit: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = RknnReadinessResult(
        ready=ready,
        model="/models/test.rknn",
        runtime_version="2.3.2",
        inference_ms=12.5 if ready else None,
        error_code=None if ready else "rknn_smoke_failed",
    )

    exit_code = rknn_smoke_main(
        lambda: SimpleNamespace(probe=lambda: result),
    )

    output = capsys.readouterr().out
    assert exit_code == expected_exit
    assert f'"ready": {str(ready).lower()}' in output
    assert '"runtime_version": "2.3.2"' in output
