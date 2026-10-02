"""Bounded read of the fixed host syslog store for one terminal container."""

import base64
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

SEGMENT = re.compile(r"^\d{10}\.jsonl$")
MAX_TAIL_BYTES = 256_000


def _tail(path: Path) -> list[bytes]:
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        stream.seek(max(0, size - MAX_TAIL_BYTES))
        raw = stream.read(MAX_TAIL_BYTES)
    if size > len(raw):
        newline = raw.find(b"\n")
        raw = raw[newline + 1:] if newline >= 0 else b""
    return raw.splitlines()


def recent_container_logs(directory: Path, container: str, now: datetime) -> bytes:
    cutoff = now - timedelta(days=7)
    marker = f" {container} ".encode("ascii")
    collected: list[bytes] = []
    paths = sorted(
        (path for path in directory.iterdir() if path.is_file() and SEGMENT.fullmatch(path.name)),
        reverse=True,
    )
    for path in paths:
        hour = datetime.strptime(path.stem, "%Y%m%d%H").replace(tzinfo=UTC)
        if hour + timedelta(hours=1) <= cutoff:
            break
        for line in reversed(_tail(path)):
            try:
                record = json.loads(line)
                received = datetime.fromisoformat(record["occurred_at"])
                payload = base64.b64decode(record["syslog_b64"], validate=True)
            except (ValueError, KeyError, TypeError):
                continue
            if received < cutoff or received > now or not payload.startswith(b"<"):
                continue
            header, separator, body = payload.partition(b" - ")
            if not separator or marker not in header:
                continue
            collected.append(received.isoformat().encode("ascii") + b" " + body.rstrip(b"\n"))
            if len(collected) >= 100:
                return b"\n".join(reversed(collected)) + b"\n"
    return b"\n".join(reversed(collected)) + (b"\n" if collected else b"")
