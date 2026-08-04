import os
from dataclasses import dataclass
from urllib.parse import urlsplit

try:
    from dotenv import load_dotenv
except ImportError:  # allows importing the package before installing requirements
    def load_dotenv(*_args, **_kwargs):
        return False

load_dotenv(".env.agent")
load_dotenv(".env")


def default_interactive_public_url(advertise_url: str, port: int) -> str:
    """Build the browser-facing relay URL from the agent's reachable address.

    The relay listens on a separate port from the HTTP API.  Keeping the host
    in one setting avoids returning 127.0.0.1 to a browser when the agent runs
    inside a container on another machine.
    """
    value = advertise_url.strip()
    parsed = urlsplit(value if "://" in value else f"http://{value}")
    host = parsed.hostname or "127.0.0.1"

    explicit = os.getenv("AGENT_INTERACTIVE_PUBLIC_URL", "").strip()
    if explicit:
        explicit_parsed = urlsplit(explicit)
        explicit_host = (explicit_parsed.hostname or "").lower()
        loopback_hosts = {"127.0.0.1", "localhost", "::1"}
        # An old systemd/.env file often contains the development default.
        # Do not carry that value into a container whose API is advertised on
        # a real LAN address; the browser would otherwise connect to itself.
        if not (explicit_host in loopback_hosts and host.lower() not in loopback_hosts):
            return explicit.rstrip("/")

    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{host}:{port}"


@dataclass(frozen=True)
class AgentSettings:
    server_url: str = os.getenv("SERVER_URL", "http://127.0.0.1:8000")
    token: str = os.getenv("AGENT_TOKEN", "change-me")
    agent_id: str = os.getenv("AGENT_ID", "dev-agent")
    name: str = os.getenv("AGENT_NAME", "开发控制终端")
    os_name: str = os.getenv("AGENT_OS", "linux")
    advertise_url: str = os.getenv("AGENT_ADVERTISE_URL", "http://127.0.0.1:8765")
    adb_serial: str = os.getenv("ADB_SERIAL", "")
    workdir: str = os.getenv("AGENT_WORKDIR", "./agent-data")
    poll_interval: float = float(os.getenv("POLL_INTERVAL_SECONDS", "0.5"))
    command_wait_seconds: float = float(os.getenv("COMMAND_WAIT_SECONDS", "15"))
    heartbeat_interval_seconds: float = float(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "10"))
    recording_time_limit_seconds: int = int(os.getenv("RECORDING_TIME_LIMIT_SECONDS", "180"))
    port: int = int(os.getenv("AGENT_PORT", "8765"))
    bind_host: str = os.getenv("AGENT_BIND_HOST", "0.0.0.0")
    interactive_host: str = os.getenv("AGENT_INTERACTIVE_HOST", "0.0.0.0")
    interactive_port: int = int(os.getenv("AGENT_INTERACTIVE_PORT", "8766"))
    interactive_public_url: str = default_interactive_public_url(
        os.getenv("AGENT_ADVERTISE_URL", "http://127.0.0.1:8765"),
        int(os.getenv("AGENT_INTERACTIVE_PORT", "8766")),
    )
    interactive_idle_timeout_seconds: int = int(os.getenv("INTERACTIVE_IDLE_TIMEOUT_SECONDS", "1200"))
    adb_server_host: str = os.getenv("ADB_SERVER_HOST", "127.0.0.1")
    adb_server_port: int = int(os.getenv("ADB_SERVER_PORT", "5037"))
settings = AgentSettings()
