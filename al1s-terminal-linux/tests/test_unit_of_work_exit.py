"""Protect the local terminal's commit-on-normal-exit contract."""

from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from al1s_terminal.persistence.unit_of_work import LocalUnitOfWork


def _unit_of_work() -> tuple[LocalUnitOfWork, MagicMock]:
    session = MagicMock(spec=Session)
    return LocalUnitOfWork(lambda: session), session


def test_normal_exit_commits() -> None:
    uow, session = _unit_of_work()
    with uow:
        pass
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    session.close.assert_called_once_with()


def test_exception_rolls_back() -> None:
    uow, session = _unit_of_work()
    with pytest.raises(ValueError, match="failed"), uow:
        raise ValueError("failed")
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


def test_early_return_and_caught_exception_commit() -> None:
    uow, session = _unit_of_work()

    def leave_early() -> None:
        with uow:
            return

    leave_early()
    session.commit.assert_called_once_with()
    session.reset_mock()
    with uow:
        try:
            raise ValueError("handled")
        except ValueError:
            pass
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
