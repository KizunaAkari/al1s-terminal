"""Consecutive sampled matches for one image-wait step in one execution."""

from __future__ import annotations

from typing import Any


def consecutive_match_count(step: dict[str, Any]) -> int:
    value = step.get("consecutive_match_count")
    if value is None:
        return 1
    if type(value) is not int or value < 1:
        raise ValueError("consecutive match count must be a positive integer")
    return value


class ImageMatchStreak:
    def __init__(self) -> None:
        self.key: str | None = None
        self.hits = 0

    def reset(self) -> None:
        self.key, self.hits = None, 0

    def accept(self, config: dict[str, Any], hit: bool) -> bool:
        if config["rule"]:
            if hit:
                self.reset()
            return hit
        if self.key != config["key"]:
            self.reset()
            self.key = config["key"]
        count = config.get("consecutive_match_count", 1)
        if count == 1:
            # Unmatched skip/assertion candidates must not erase the main
            # wait's streak. A matched branch leaves or retries that wait.
            if hit:
                self.reset()
            return hit
        self.hits = self.hits + 1 if hit else 0
        if self.hits < count:
            return False
        self.reset()
        return True
