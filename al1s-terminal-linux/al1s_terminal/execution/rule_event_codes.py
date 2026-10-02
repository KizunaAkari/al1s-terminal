"""Bounded diagnostic codes for independent-rule telemetry, never task state."""

from collections.abc import Callable

RULE_KINDS = frozenset({"rule_started", "rule_succeeded", "rule_failed"})


def bind_module_events(
    emit: Callable[[str, int], None], definition_key: str
) -> Callable[[str, int], None]:
    def bound(kind: str, number: int) -> None:
        emit(f"{kind}:{definition_key}" if kind in RULE_KINDS else kind, number)

    return bound


def rule_event_code(kind: str, index: int | None) -> str | None:
    phase, separator, key = kind.partition(":")
    if phase not in RULE_KINDS or not separator or type(index) is not int or not 0 <= index < 50:
        return None
    code = f"maa_{phase}:{key}:{index}"
    if not key or not code.isascii() or len(code) > 100:
        raise ValueError("Independent-rule event identity is invalid")
    return code
