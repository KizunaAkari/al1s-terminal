from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from al1s_terminal.persistence.migrations import upgrade_database


def _config(database_url: str) -> Config:
    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_empty_database_upgrades_and_downgrades(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    upgrade_database(database_url)
    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) >= {
        "terminal_installation",
        "inbox_work_items",
        "outbox_reports",
        "local_transitions",
        "package_manifests",
        "cached_resources",
        "work_item_resources",
        "target_device_observations",
        "local_artifacts",
    }
    engine.dispose()

    command.downgrade(_config(database_url), "base")

    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    engine.dispose()


def test_migration_upgrade_is_repeatable(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'repeat.db').as_posix()}"
    upgrade_database(database_url)
    upgrade_database(database_url)


def test_fifo_upgrade_preserves_existing_queued_work(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'fifo-upgrade.db').as_posix()}"
    command.upgrade(_config(database_url), "20260901_0007")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO inbox_work_items (id, kind, remote_id, content_hash, status, "
                "available_at, payload, created_at, updated_at, row_version) "
                "VALUES (:id, 'formal_task', :id, :hash, 'queued', :stamp, '{}', :stamp, :stamp, 1)"
            ),
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "hash": "a" * 64,
                "stamp": "2026-09-06 12:00:00",
            },
        )
    engine.dispose()
    upgrade_database(database_url)
    engine = create_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT id, status, created_at, queue_enqueued_at, queue_order_id "
                "FROM inbox_work_items"
            )
        ).one()
        assert row.status == "queued"
        assert row.id == row.queue_order_id
        assert row.created_at == row.queue_enqueued_at
    engine.dispose()


def test_stage_5a_database_upgrades_to_package_cache(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'upgrade.db').as_posix()}"
    command.upgrade(_config(database_url), "20260831_0001")

    upgrade_database(database_url)

    engine = create_engine(database_url)
    assert set(inspect(engine).get_table_names()) >= {
        "package_manifests",
        "cached_resources",
        "work_item_resources",
    }
    engine.dispose()


def test_stage_5b_database_upgrades_to_target_device_observations(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'device-upgrade.db').as_posix()}"
    command.upgrade(_config(database_url), "20260831_0002")

    upgrade_database(database_url)

    engine = create_engine(database_url)
    assert "target_device_observations" in inspect(engine).get_table_names()
    engine.dispose()


def test_stage_5c_database_upgrades_to_local_artifacts(tmp_path: Path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'artifact-upgrade.db').as_posix()}"
    command.upgrade(_config(database_url), "20260831_0003")

    upgrade_database(database_url)

    engine = create_engine(database_url)
    assert "local_artifacts" in inspect(engine).get_table_names()
    engine.dispose()
