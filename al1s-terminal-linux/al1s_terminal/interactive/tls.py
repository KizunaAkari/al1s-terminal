"""Optional terminal-side TLS, without proxying video through the platform."""

import ssl
from pathlib import Path


def server_context(cert: Path | None, key: Path | None) -> ssl.SSLContext | None:
    if cert is None and key is None:
        return None
    if cert is None or key is None:
        raise ValueError("Both scrcpy TLS certificate and private key are required")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    return context
