import os
from dataclasses import replace

import pytest

from terminal_deployer.models import DeploymentError, HostDeploymentConfig
from terminal_deployer.prepare import prepare_host


def configuration(tmp_path):
    for directory in ("data", "adb-home", "models", "certs"):
        (tmp_path / directory).mkdir(mode=0o700)
    compose, env = tmp_path / "compose.yaml", tmp_path / "terminal.env"
    compose.write_text("services: {}")
    env.write_text("")
    return HostDeploymentConfig(
        tmp_path,
        compose,
        env,
        tmp_path / "data",
        tmp_path / "adb-home",
        tmp_path / "models",
        tmp_path / "certs",
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX lock mode contract")
def test_prepare_preserves_existing_identity_and_lock(tmp_path):
    config = configuration(tmp_path)
    identity = config.data_dir / "identity"
    identity.write_text("existing")
    config.lock_path.write_text("preserve")
    prepare_host(config)
    prepare_host(config)
    assert identity.read_text() == "existing"
    assert config.lock_path.read_text() == "preserve"
    assert all(
        path.is_dir() for path in (config.artifact_dir, config.deployment_dir, config.state_dir)
    )


def test_missing_data_is_not_replaced_by_empty_directory(tmp_path):
    config = replace(configuration(tmp_path), data_dir=tmp_path / "missing")
    with pytest.raises(DeploymentError, match="existing_terminal_directories_required"):
        prepare_host(config)
    assert not config.data_dir.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink privilege contract")
def test_prepare_rejects_linked_artifact_directory(tmp_path):
    config = configuration(tmp_path)
    config.artifact_dir.symlink_to(config.data_dir, target_is_directory=True)
    with pytest.raises(DeploymentError, match="unsafe_upgrade_directory"):
        prepare_host(config)
