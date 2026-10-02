from __future__ import annotations

import hmac
import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socket import socket
from threading import BoundedSemaphore
from uuid import UUID

from pydantic import ValidationError

from al1s_terminal.host_management.service import (
    CancelRequest,
    HostMaintenance,
    MaintenanceError,
    RecoveryRequest,
    RestartRequest,
    UpgradeRequest,
)


class LimitedServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        self.slots = BoundedSemaphore(8)
        super().__init__(address, handler)

    def process_request(
        self,
        request: socket | tuple[bytes, socket],
        client_address: tuple[str, int],
    ) -> None:
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(
        self,
        request: socket | tuple[bytes, socket],
        client_address: tuple[str, int],
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def create_server(
    address: tuple[str, int], service: HostMaintenance, token: str
) -> ThreadingHTTPServer:
    if len(token) < 32:
        raise ValueError("host_management_token_too_short")

    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, _format: str, *args: object) -> None:
            pass  # No request bodies, URLs with secrets, or credentials in standard access logs.

        def reply(self, code: int, payload: object) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self) -> bool:
            if hmac.compare_digest(
                self.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()
            ):
                return True
            self.reply(401, {"code": "unauthorized"})
            return False

        def do_GET(self) -> None:
            if not self.authorized():
                return
            try:
                if self.path == "/v1/health":
                    self.reply(200, asdict(service.driver.observe()))
                    return
                if self.path == "/v1/logs":
                    self.reply(200, service.driver.logs())
                    return
                if self.path.startswith("/v1/commands/"):
                    result = service.get(UUID(self.path.removeprefix("/v1/commands/")))
                    self.reply(200 if result else 404, result or {"code": "not_found"})
                    return
                self.reply(404, {"code": "not_found"})
            except ValueError:
                self.reply(400, {"code": "invalid_identifier"})
            except Exception:
                self.reply(503, {"code": "observation_unavailable"})

        def do_POST(self) -> None:
            if not self.authorized():
                return
            if self.path not in {"/v1/commands", "/v1/upgrades/recover", "/v1/commands/cancel"}:
                self.reply(404, {"code": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192 or self.headers.get("Transfer-Encoding"):
                    self.reply(413, {"code": "invalid_body_length"})
                    return
                body = self.rfile.read(length)
                if self.path == "/v1/commands/cancel":
                    self.reply(200, service.cancel(CancelRequest.model_validate_json(body)))
                    return
                if self.path == "/v1/upgrades/recover":
                    self.reply(200, service.recover(RecoveryRequest.model_validate_json(body)))
                    return
                decoded = json.loads(body)
                model = (
                    UpgradeRequest
                    if isinstance(decoded, dict) and decoded.get("action") == "upgrade_container"
                    else RestartRequest
                )
                request = model.model_validate(decoded)
                self.reply(202, service.submit(request))
            except (ValueError, ValidationError):
                self.reply(400, {"code": "invalid_request"})
            except MaintenanceError as exc:
                self.reply(409, {"code": str(exc)})
            except Exception:
                self.reply(503, {"code": "management_unavailable"})

    return LimitedServer(address, Handler)
