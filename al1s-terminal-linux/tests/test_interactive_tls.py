import ssl
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from al1s_terminal.interactive.tls import server_context


def test_tls_optional_and_partial_configuration_rejected():
    assert server_context(None, None) is None
    with pytest.raises(ValueError):
        server_context(Path("cert"), None)


def test_server_loads_configured_pair_with_tls12_minimum():
    context = Mock()
    with patch("al1s_terminal.interactive.tls.ssl.SSLContext", return_value=context) as factory:
        assert server_context(Path("cert"), Path("key")) is context
    factory.assert_called_once_with(ssl.PROTOCOL_TLS_SERVER)
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2
    context.load_cert_chain.assert_called_once_with(Path("cert"), Path("key"))
