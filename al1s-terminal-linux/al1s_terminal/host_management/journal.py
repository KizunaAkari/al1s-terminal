from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection

from alembic import command
from alembic.config import Config
from sqlalchemy import Float, Integer, String, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class Command(Base):
    __tablename__ = "host_commands"
    command_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    action: Mapped[str] = mapped_column(String(32))
    release_id: Mapped[str | None] = mapped_column(String(36))
    request_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24))
    expires_at: Mapped[float] = mapped_column(Float)
    accepted_at: Mapped[float] = mapped_column(Float)
    started_at: Mapped[float | None] = mapped_column(Float)
    recovery_start_at: Mapped[float | None] = mapped_column(Float)
    late_state: Mapped[str | None] = mapped_column(String(24))
    previous_boot: Mapped[str] = mapped_column(String(64))
    previous_container: Mapped[str] = mapped_column(String(64))
    previous_start: Mapped[str] = mapped_column(String(64))
    confirmed_upgrade_id: Mapped[str | None] = mapped_column(String(100))
    error_code: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)


class Journal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.engine = create_engine(
            f"sqlite:///{path.resolve().as_posix()}", connect_args={"timeout": 10}
        )

        @event.listens_for(self.engine, "connect")
        def durable(connection: Connection, _record: object) -> None:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")

        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def transaction(self) -> Session:
        return Session(self.engine, expire_on_commit=False)

    @staticmethod
    def active(session: Session) -> Command | None:
        return session.scalar(
            select(Command)
            .where(
                Command.state.in_(("accepted", "executing", "recovering")),
            )
            .order_by(Command.accepted_at)
            .limit(1)
        )

    def close(self) -> None:
        self.engine.dispose()


def utc_timestamp() -> float:
    return datetime.now(UTC).timestamp()
