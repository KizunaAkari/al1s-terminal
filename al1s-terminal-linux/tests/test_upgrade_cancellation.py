from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from al1s_terminal.host_management.upgrades import HostUpgradeExecutor, UpgradeCancelled


@pytest.mark.parametrize("cancel", [True, False])
def test_download_cancel_never_reaches_install_and_survives_executor_restart(
    tmp_path, monkeypatch, cancel
):
    config = SimpleNamespace(state_dir=tmp_path / "states", artifact_dir=tmp_path / "artifacts")
    client = Mock()
    cancelled = [False]
    installed = []

    def checkpoint():
        if cancelled[0]:
            raise UpgradeCancelled()

    def download(*args):
        cancelled[0] = cancel
        client.checkpoint()

    client.download.side_effect = download

    class Deployer:
        def __init__(self, config):
            pass

        def deploy(self, manifest, *, prepare, timeout_seconds):
            prepare(100)
            installed.append(True)

    monkeypatch.setattr("al1s_terminal.host_management.upgrades.TerminalDeployer", Deployer)
    executor = HostUpgradeExecutor(config, lambda: client)
    if cancel:
        with pytest.raises(UpgradeCancelled):
            executor.run_cancellable("cancel-test", uuid4(), 0, checkpoint, checkpoint)
        assert HostUpgradeExecutor(config, lambda: client).result("cancel-test") == "cancelled"
        assert not installed
        assert executor.states.unresolved_upgrade() is None
    else:
        executor.run_cancellable("cancel-test", uuid4(), 0, checkpoint, checkpoint)
        assert installed == [True]
    client.close.assert_called_once()
