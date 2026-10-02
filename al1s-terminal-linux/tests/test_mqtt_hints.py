from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from al1s_terminal.transport.delivery_models import TerminalMqttSessionPayload
from al1s_terminal.transport.mqtt_hints import MqttHintListener


class FakeMqttClient:
    def __init__(self) -> None:
        self.on_connect = None
        self.on_message = None
        self.connected: tuple[str, int, int] | None = None
        self.started = False
        self.stopped = False
        self.credentials: tuple[str, str] | None = None
        self.ca_certs: str | None = None

    def username_pw_set(self, username: str, password: str) -> None:
        self.credentials = (username, password)

    def tls_set(self, *, ca_certs: str | None = None) -> None:
        self.ca_certs = ca_certs

    def connect(self, host: str, port: int, keepalive: int) -> None:
        self.connected = (host, port, keepalive)

    def loop_start(self) -> None:
        self.started = True

    def disconnect(self) -> None:
        return None

    def loop_stop(self) -> None:
        self.stopped = True

    def subscribe(self, topic: str, qos: int) -> None:
        del topic, qos


def test_hint_wakes_reconciliation_and_session_is_replaced(monkeypatch: object) -> None:
    clients: list[FakeMqttClient] = []

    def create_client(**kwargs: object) -> FakeMqttClient:
        del kwargs
        client = FakeMqttClient()
        clients.append(client)
        return client

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "al1s_terminal.transport.mqtt_hints.mqtt.Client",
        create_client,
    )
    wakes: list[bool] = []
    now = datetime(2026, 9, 1, 8, tzinfo=UTC)
    listener = MqttHintListener(lambda: wakes.append(True), clock=lambda: now)
    first = _session(now, "first")
    second = _session(now + timedelta(minutes=1), "second")

    listener.apply(first)
    first_client = clients[0]
    assert first_client.credentials == (first.username, first.password)
    assert first_client.on_message is not None
    first_client.on_message(
        first_client,
        None,
        SimpleNamespace(topic=first.topic, payload=b"{}"),
    )
    listener.apply(second)

    assert wakes == [True]
    assert first_client.stopped is True
    assert clients[1].started is True
    listener.close()


def test_hint_uses_explicit_private_ca_for_tls(monkeypatch: object, tmp_path: Path) -> None:
    clients: list[FakeMqttClient] = []

    def create_client(**kwargs: object) -> FakeMqttClient:
        del kwargs
        client = FakeMqttClient()
        clients.append(client)
        return client

    monkeypatch.setattr(  # type: ignore[attr-defined]
        "al1s_terminal.transport.mqtt_hints.mqtt.Client",
        create_client,
    )
    ca_file = tmp_path / "platform-ca.crt"
    ca_file.write_text("test-ca", encoding="utf-8")
    now = datetime(2026, 9, 1, 8, tzinfo=UTC)
    session = _session(now, "tls").model_copy(update={"tls_enabled": True, "broker_port": 8883})

    listener = MqttHintListener(lambda: None, clock=lambda: now, ca_file=ca_file)
    listener.apply(session)

    assert clients[0].ca_certs == str(ca_file)
    listener.close()


def _session(issued_at: datetime, suffix: str) -> TerminalMqttSessionPayload:
    return TerminalMqttSessionPayload(
        session_id=uuid4(),
        broker_host="mqtt.example.test",
        broker_port=1883,
        tls_enabled=False,
        client_id=f"client-{suffix}",
        username=f"user-{suffix}",
        password=f"password-{suffix}",
        topic=f"al1s/v1/terminals/{uuid4()}/hints",
        qos=1,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(hours=1),
    )
