"""Bound ordinary evidence creation; failure capture remains independently available."""

from collections.abc import Callable
from typing import Any

MAX_SCREENSHOTS_PER_SCRIPT = 256


class CaptureBudget:
    def __init__(self, capture: Callable[[], Any]) -> None:
        self._capture = capture
        self._count = 0

    def __call__(self) -> Any:
        if self._count >= MAX_SCREENSHOTS_PER_SCRIPT:
            return {"discarded": "screenshot_limit"}
        result = self._capture()
        self._count += 1
        return result
