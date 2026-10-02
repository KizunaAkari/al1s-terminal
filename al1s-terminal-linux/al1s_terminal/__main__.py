from __future__ import annotations

import argparse
import json
import signal
from collections.abc import Sequence

from pydantic import ValidationError

from al1s_terminal.app.config import TerminalSettings
from al1s_terminal.app.preflight import inspect_environment
from al1s_terminal.app.runtime import TerminalRuntime


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="al1s-terminal")
    parser.add_argument("command", choices=("bootstrap", "run", "check"), nargs="?", default="run")
    args = parser.parse_args(argv)
    try:
        settings = TerminalSettings()
    except ValidationError as error:
        # Never print validation input: it can contain registration codes or URL credentials.
        print(json.dumps({"ready": False, "code": "invalid_configuration",
                          "fields": [list(item["loc"]) for item in error.errors(
                              include_input=False, include_context=False)]}))
        return 2
    if args.command == "check":
        report = inspect_environment(settings)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0 if report["ready"] else 2
    runtime = TerminalRuntime(settings)
    stopping = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    try:
        if args.command == "bootstrap":
            print(json.dumps(runtime.bootstrap(), ensure_ascii=False, sort_keys=True))
        else:
            runtime.run(stop_requested=lambda: stopping)
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
