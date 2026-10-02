"""Private serial worker: models may live across tasks, task data may not."""

import json
import os
import sys
import threading
import time
from pathlib import Path

from al1s_terminal.lineup.resources import LineupResources
from al1s_terminal.lineup.session import atomic_json
from al1s_terminal.lineup.worker import recognize

IDLE_SECONDS = 60


def watch_parent(parent: int) -> None:
    while os.getppid() == parent:
        time.sleep(1)
    os._exit(1)


def serve(assets: Path, directory: Path, parent: int, idle_seconds: float = IDLE_SECONDS) -> None:
    threading.Thread(target=watch_parent, args=(parent,), daemon=True).start()
    resources = LineupResources(assets, assets / "ocr")
    idle_since = time.monotonic()
    request = directory / "request.json"
    while time.monotonic() - idle_since < idle_seconds:
        if not request.is_file():
            time.sleep(0.05)
            continue
        if request.stat().st_size > 8192:
            raise ValueError("lineup_request_too_large")
        value = json.loads(request.read_text(encoding="utf-8"))
        request.unlink()
        result = recognize(
            Path(value["image"]),
            assets,
            assets / "ocr",
            value["hint"],
            value["mode"],
            resources=resources,
        )
        atomic_json(directory / "response.json", {"id": value["id"], "result": result})
        idle_since = time.monotonic()


if __name__ == "__main__":
    serve(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]))
