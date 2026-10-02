import json

import pytest
from pydantic import ValidationError

from al1s_terminal.app.config import TerminalSettings
from al1s_terminal.app.preflight import inspect_environment


@pytest.mark.parametrize("url", [
    "https://", "https://user:secret@host", "https://host?token=secret",
    "https://host#fragment", "https://host:99999", "https://bad host",
])
def test_invalid_platform_endpoint(url):
    with pytest.raises(ValidationError):
        TerminalSettings(_env_file=None, platform_url=url)


def test_check_does_not_create_data_or_expose_registration(tmp_path):
    directory = tmp_path / "new-data"
    settings = TerminalSettings(_env_file=None, data_dir=directory,
                                registration_code="sensitive-one-time-code")
    result = inspect_environment(settings)
    assert not directory.exists()
    assert "sensitive-one-time-code" not in json.dumps(result)
    assert next(x for x in result["checks"] if x["name"] == "identity")["available"]


def test_missing_identity_is_not_ready(tmp_path):
    settings = TerminalSettings(_env_file=None, data_dir=tmp_path, registration_code=None)
    assert inspect_environment(settings)["ready"] is False
