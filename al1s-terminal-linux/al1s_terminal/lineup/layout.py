"""Bounded horizontal lineup grouping, independent of attack/defense semantics."""

from itertools import pairwise
from statistics import median
from typing import Any


def horizontal_row(
    items: list[dict[str, Any]], count: int | None = None, *, partial: bool = False
) -> list[dict[str, Any]]:
    rows: list[list[dict[str, Any]]] = []
    for item in sorted(items[:100], key=lambda item: item["box"][1]):
        _x, y, w, h = item["box"]
        if min(w, h) <= 0:
            continue
        center = y + h / 2
        row = next(
            (
                row
                for row in rows
                if abs(center - median(s["box"][1] + s["box"][3] / 2 for s in row))
                < max(h, median(s["box"][3] for s in row)) * 0.65
            ),
            None,
        )
        if row is None:
            rows.append([item])
        else:
            row.append(item)
    candidates = [
        r
        for r in rows
        if (partial and 1 <= len(r) <= (count or 12))
        or len(r) == count
        or (count is None and len(r) in (6, 12))
    ]
    if not candidates:
        return []
    # Header avatars and other isolated UI elements must never become members.
    row = max(candidates, key=lambda r: median(s["box"][1] for s in r))
    row = sorted(row, key=lambda item: item["box"][0])
    if any(a["box"][0] + a["box"][2] * 0.7 > b["box"][0] for a, b in pairwise(row)):
        return []
    return row


def assign_teams(
    row: list[dict[str, Any]],
    hint: str,
    attack_side: str | None,
) -> dict[str, list[dict[str, Any]]]:
    if 1 <= len(row) <= 6 and hint in ("attack", "defense"):
        return {hint: row}
    if 2 <= len(row) <= 12 and hint in ("auto", "left_attack", "right_attack"):
        gaps = [b["box"][0] - a["box"][0] - a["box"][2] for a, b in pairwise(row)]
        split = max(range(len(gaps)), key=gaps.__getitem__) + 1
        ordinary = [gap for i, gap in enumerate(gaps) if i != split - 1]
        if not (1 <= split <= 6 and 1 <= len(row) - split <= 6):
            return {}
        # A visible inter-team gap is necessary; never infer six from a missing detection.
        if ordinary and gaps[split - 1] < max(ordinary) * 1.5:
            return {}
        side = attack_side if hint == "auto" else ("left" if hint == "left_attack" else "right")
        if side == "left":
            return dict(attack=row[:split], defense=row[split:])
        if side == "right":
            return dict(attack=row[split:], defense=row[:split])
    return {}
