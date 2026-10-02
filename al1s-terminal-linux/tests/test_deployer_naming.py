from terminal_deployer.configuration import deployment_config


def test_default_terminal_container_name(monkeypatch):
    monkeypatch.delenv("AL1S_HOST_CONTAINER", raising=False)
    assert deployment_config().container_name == "al1s-terminal-linux"


def test_upgrade_uses_same_target_as_host_manager(monkeypatch):
    monkeypatch.setenv("AL1S_HOST_CONTAINER", "al1s-terminal-linux-test")
    assert deployment_config().container_name == "al1s-terminal-linux-test"
