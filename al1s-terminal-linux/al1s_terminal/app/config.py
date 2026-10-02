from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TerminalSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AL1S_TERMINAL_",
        env_file=".env",
        extra="ignore",
    )

    platform_url: str = "http://127.0.0.1:8000"
    data_dir: Path = Path("./data")
    display_name: str = Field(default="AL-1S Linux Terminal", min_length=1, max_length=120)
    agent_version: str = Field(default="0.1.0", min_length=1, max_length=64)
    registration_code: SecretStr | None = None
    heartbeat_interval_seconds: float = Field(default=10.0, ge=2.0, le=300.0)
    reconciliation_interval_seconds: float = Field(default=30.0, ge=5.0, le=600.0)
    http_timeout_seconds: float = Field(default=15.0, ge=1.0, le=120.0)
    tls_ca_file: Path | None = None
    outbox_batch_size: int = Field(default=50, ge=1, le=50)
    artifact_batch_size: int = Field(default=10, ge=1, le=20)
    gc_batch_size: int = Field(default=20, ge=1, le=50)
    gc_interval_seconds: float = Field(default=300.0, ge=30.0, le=3_600.0)
    gc_retention_seconds: int = Field(default=24 * 60 * 60, ge=5 * 60)
    adb_path: Path | None = None
    maa_ocr_model_dir: Path | None = None
    maa_yolo_provider: str | None = "al1s_terminal.providers.rknn_yolo:RknnYoloProvider"
    maa_adb_screencap_methods: int | None = Field(default=None, ge=1)
    maa_adb_input_methods: int | None = Field(default=None, ge=1)
    maa_screenshot_mode: Literal["raw", "scaled"] = "raw"
    maa_screenshot_short_side: int = Field(default=720, ge=360, le=4_320)
    maa_adb_command_timeout_seconds: int = Field(default=30, ge=5, le=120)
    minimum_available_memory_bytes: int = Field(default=256 * 1024**2, ge=128 * 1024**2)
    minimum_available_storage_bytes: int = Field(default=1024**3, ge=256 * 1024**2)
    recording_storage_reserve_bytes: int = Field(default=512 * 1024**2, ge=128 * 1024**2)
    scrcpy_relay_host: str = "0.0.0.0"
    scrcpy_relay_port: int = Field(default=8766, ge=1, le=65_535)
    scrcpy_public_url: str | None = None
    scrcpy_server_path: Path | None = None
    scrcpy_server_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    scrcpy_idle_timeout_seconds: float = Field(default=1800, ge=30, le=86_400)
    scrcpy_tls_cert_file: Path | None = None
    scrcpy_tls_key_file: Path | None = None

    @field_validator("platform_url")
    @classmethod
    def normalize_platform_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        cls._validate_endpoint(normalized, {"http", "https"})
        return normalized

    @field_validator("scrcpy_public_url")
    @classmethod
    def normalize_scrcpy_public_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        cls._validate_endpoint(normalized, {"ws", "wss"})
        return normalized

    @staticmethod
    def _validate_endpoint(value: str, schemes: set[str]) -> None:
        parsed = urlsplit(value)
        if (parsed.scheme not in schemes or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or any(char.isspace() for char in value)):
            raise ValueError("endpoint requires a hostname and must not contain credentials")
        # Accessing port validates malformed/non-numeric and out-of-range ports.
        _ = parsed.port

    @field_validator("tls_ca_file")
    @classmethod
    def validate_tls_ca_file(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_file():
            raise ValueError("tls_ca_file must reference an existing CA certificate")
        return value

    @property
    def database_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'terminal.db').resolve().as_posix()}"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "secrets" / "terminal-credential"
