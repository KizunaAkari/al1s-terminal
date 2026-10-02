from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from paho.mqtt.properties import Properties
from paho.mqtt.reasoncodes import ReasonCode

from al1s_terminal.transport.delivery_models import TerminalMqttSessionPayload


class MqttHintConnectionError(RuntimeError):
    pass


class MqttHintListener:
    """Receives best-effort wake hints; HTTP reconciliation remains authoritative."""

    def __init__(
        self,
        wake: Callable[[], None],
        *,
        clock: Callable[[], datetime] | None = None,
        ca_file: Path | None = None,
    ) -> None:
        self._wake = wake
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._ca_file = ca_file
        self._client: mqtt.Client | None = None
        self._session: TerminalMqttSessionPayload | None = None

    @property
    def refresh_at(self) -> datetime | None:
        session = self._session
        if session is None:
            return None
        lifetime = session.expires_at - session.issued_at
        return session.expires_at - min(lifetime / 3, lifetime / 2)

    def apply(self, session: TerminalMqttSessionPayload) -> None:
        with self._lock:
            if self._session is not None and self._session.session_id == session.session_id:
                self._session = session
                return
            self._close_locked()
            client = self._build_client(session)
            try:
                client.connect(session.broker_host, session.broker_port, keepalive=30)
                client.loop_start()
            except OSError as exc:
                client.disconnect()
                raise MqttHintConnectionError("MQTT hint connection failed") from exc
            self._client = client
            self._session = session

    def needs_refresh(self) -> bool:
        refresh_at = self.refresh_at
        return refresh_at is None or self._clock() >= refresh_at

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _build_client(self, session: TerminalMqttSessionPayload) -> mqtt.Client:
        client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=session.client_id,
            protocol=mqtt.MQTTv5,
        )
        client.username_pw_set(session.username, session.password)
        if session.tls_enabled:
            client.tls_set(ca_certs=str(self._ca_file) if self._ca_file is not None else None)

        def on_connect(
            connected: mqtt.Client,
            userdata: object,
            flags: mqtt.ConnectFlags,
            reason_code: ReasonCode,
            properties: Properties | None,
        ) -> None:
            del userdata, flags, properties
            if not reason_code.is_failure:
                connected.subscribe(session.topic, qos=session.qos)

        def on_message(
            connected: mqtt.Client,
            userdata: object,
            message: mqtt.MQTTMessage,
        ) -> None:
            del connected, userdata
            if message.topic == session.topic:
                self._wake()

        client.on_connect = on_connect
        client.on_message = on_message
        return client

    def _close_locked(self) -> None:
        if self._client is not None:
            self._client.disconnect()
            self._client.loop_stop()
        self._client = None
        self._session = None
