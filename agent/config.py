import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ImportError:  # allows importing the package before installing requirements
    def load_dotenv(*_args, **_kwargs):
        return False

load_dotenv(".env.agent")
load_dotenv(".env")


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
    interactive_public_url: str = os.getenv("AGENT_INTERACTIVE_PUBLIC_URL", "ws://127.0.0.1:8766")
    interactive_idle_timeout_seconds: int = int(os.getenv("INTERACTIVE_IDLE_TIMEOUT_SECONDS", "1200"))
    adb_server_host: str = os.getenv("ADB_SERVER_HOST", "127.0.0.1")
    adb_server_port: int = int(os.getenv("ADB_SERVER_PORT", "5037"))
settings = AgentSettings()
