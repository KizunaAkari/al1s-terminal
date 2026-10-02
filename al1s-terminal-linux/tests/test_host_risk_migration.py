from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from al1s_terminal.host_management import journal as module
from al1s_terminal.host_management.journal import Command, Journal


def test_host_0001_upgrade_preserves_commands_and_defaults_risk_to_none(tmp_path):
    path = tmp_path / "host.db"
    engine = create_engine(f"sqlite:///{path.as_posix()}")
    config = Config()
    config.set_main_option("script_location", str(Path(module.__file__).parent / "migrations"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "host_0001")
        connection.execute(text(
            "INSERT INTO host_commands (command_id, action, request_hash, state, expires_at, "
            "accepted_at, previous_boot, previous_container, previous_start, version) "
            "VALUES ('old', 'restart_host', 'hash', 'executing', 1500, 1000, 'boot', 'c', 's', 2)"
        ))
    engine.dispose()
    current = Journal(path)
    try:
        with current.transaction() as session:
            row = session.get(Command, "old")
            assert row.state == "executing"
            assert row.version == 2
            assert row.confirmed_upgrade_id is None
            assert row.release_id is None
            assert session.scalar(text("SELECT version_num FROM alembic_version")) == "host_0003"
    finally:
        current.close()
