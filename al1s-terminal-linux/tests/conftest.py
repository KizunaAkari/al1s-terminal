from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from al1s_terminal.persistence.base import create_local_engine
from al1s_terminal.persistence.migrations import upgrade_database


@pytest.fixture
def local_engine(tmp_path: Path) -> Iterator[Engine]:
    database_url = f"sqlite:///{(tmp_path / 'terminal.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_local_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()
