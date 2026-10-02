from pathlib import Path

from terminal_deployer.configuration import deployment_config


def test_defaults_use_deployment_directory(monkeypatch):
    for name in ("AL1S_DEPLOY_ROOT", "AL1S_DEPLOY_COMPOSE", "AL1S_DEPLOY_ENV_FILE"):
        monkeypatch.delenv(name, raising=False)
    config = deployment_config()
    assert config.compose_file == config.root / "deployment/compose.arm64.yaml"
    assert config.env_file == config.root / "deployment/terminal.arm64.env"


def test_explicit_new_identity_and_model_paths_are_preserved(monkeypatch):
    monkeypatch.setenv("AL1S_DEPLOY_ROOT", "/srv/al1s")
    monkeypatch.setenv("AL1S_DEPLOY_DATA_DIR", "/srv/al1s/data-new-platform")
    monkeypatch.setenv("AL1S_DEPLOY_MODEL_DIR", "/srv/shared/models")
    config = deployment_config()
    assert config.data_dir == Path("/srv/al1s/data-new-platform")
    assert config.model_dir == Path("/srv/shared/models")
