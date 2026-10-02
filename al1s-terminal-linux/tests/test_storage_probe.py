from types import SimpleNamespace
from unittest.mock import Mock

from al1s_terminal.providers.storage import runtime_storage


def test_probe_uses_runtime_filesystem(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    probe = Mock(return_value=SimpleNamespace(total=100, used=70, free=20))
    monkeypatch.setattr("al1s_terminal.providers.storage.shutil.disk_usage", probe)
    assert runtime_storage(tmp_path) == {
        "directory": str(runtime),
        "total_bytes": 100,
        "used_bytes": 70,
        "available_bytes": 20,
    }
    probe.assert_called_once_with(runtime)


def test_failed_probe_is_unknown_not_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "al1s_terminal.providers.storage.shutil.disk_usage", Mock(side_effect=OSError)
    )
    assert runtime_storage(tmp_path) is None
