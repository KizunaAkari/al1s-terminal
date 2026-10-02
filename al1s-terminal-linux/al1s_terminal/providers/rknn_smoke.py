from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any, Protocol

import numpy as np

from al1s_terminal.providers.rknn_yolo import RknnYoloProvider


class RknnProvider(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def error(self) -> str: ...

    def status(self) -> dict[str, Any]: ...

    def detect(self, image: Any, params: dict[str, Any]) -> dict[str, Any]: ...

    def close(self) -> None: ...


class ReadinessProbe(Protocol):
    def probe(self) -> RknnReadinessResult: ...


@dataclass(frozen=True, slots=True)
class RknnReadinessResult:
    ready: bool
    model: str | None = None
    runtime_version: str | None = None
    inference_ms: float | None = None
    output_shapes: tuple[tuple[int, ...], ...] = ()
    cached: bool = False
    error_code: str | None = None
    diagnostic: str | None = None


class RknnRuntimeReadinessProbe:
    """Run one harmless inference before advertising the YOLO capability."""

    def __init__(
        self,
        provider_factory: Callable[[], RknnProvider] = RknnYoloProvider,
    ) -> None:
        self._provider_factory = provider_factory
        self._successful: RknnReadinessResult | None = None

    def probe(self) -> RknnReadinessResult:
        if self._successful is not None:
            return replace(self._successful, cached=True)
        provider: RknnProvider | None = None
        try:
            provider = self._provider_factory()
            if not provider.available:
                return RknnReadinessResult(
                    ready=False,
                    error_code="rknn_runtime_unavailable",
                    diagnostic=provider.error[:512],
                )
            status = provider.status()
            input_size = status.get("input_size")
            width, height = _input_dimensions(input_size)
            result = provider.detect(
                np.zeros((height, width, 3), dtype=np.uint8),
                {"confidence": 1.0, "max_detections": 1},
            )
            readiness = RknnReadinessResult(
                ready=True,
                model=_optional_string(status.get("model")),
                runtime_version=_optional_string(status.get("runtime_version")),
                inference_ms=float(result["inference_ms"]),
                output_shapes=tuple(
                    tuple(int(dimension) for dimension in shape)
                    for shape in result.get("output_shapes", [])
                ),
            )
            self._successful = readiness
            return readiness
        except Exception as exc:
            return RknnReadinessResult(
                ready=False,
                error_code="rknn_smoke_failed",
                diagnostic=str(exc)[:512],
            )
        finally:
            if provider is not None:
                provider.close()


def _input_dimensions(value: Any) -> tuple[int, int]:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise ValueError("RKNN provider did not report a valid input size")
    width, height = (int(dimension) for dimension in value)
    if width < 1 or height < 1:
        raise ValueError("RKNN provider input size must be positive")
    return width, height


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def main(
    probe_factory: Callable[[], ReadinessProbe] = RknnRuntimeReadinessProbe,
) -> int:
    """Run the hardware readiness probe as a deployment-safe CLI smoke test."""

    result = probe_factory().probe()
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))
    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
