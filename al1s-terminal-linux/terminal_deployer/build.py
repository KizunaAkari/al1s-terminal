from __future__ import annotations

import argparse
import shutil
import tempfile
import zipapp
from collections.abc import Sequence
from pathlib import Path


def build_zipapp(source_root: Path, target: Path) -> Path:
    package = source_root / "terminal_deployer"
    if not (package / "__main__.py").is_file():
        raise ValueError("terminal_deployer package is missing")
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="al1s-terminal-deployer-") as temporary:
        staging = Path(temporary)
        shutil.copytree(package, staging / "terminal_deployer")
        zipapp.create_archive(
            staging,
            target=target,
            interpreter="/usr/bin/env python3",
            main="terminal_deployer.__main__:main",
            compressed=True,
        )
    return target


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="al1s-terminal-deployer-build")
    parser.add_argument("target", type=Path)
    args = parser.parse_args(argv)
    source_root = Path(__file__).resolve().parents[1]
    print(build_zipapp(source_root, args.target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
