import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class LocalHandler(BaseHTTPRequestHandler):
    device: Any = None

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"ok": True, "service": "control-agent"})
        if self.path == "/api/device":
            try:
                return self._json(200, self.device.state())
            except Exception as exc:
                return self._json(500, {"error": str(exc)})
        if self.path == "/editor":
            body = (
                "<!doctype html><meta charset='utf-8'><title>Agent Script Editor</title>"
                "<h1>Agent Script Editor</h1>"
                "<p>脚本保存在终端，请通过平台控制中心打开和编辑。</p>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None
        return self._json(404, {"error": "not found"})

    def log_message(self, *_args):
        return


def start_local_api(host: str, port: int, device):
    class DeviceHandler(LocalHandler):
        pass

    DeviceHandler.device = device
    server = ThreadingHTTPServer((host, port), DeviceHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="agent-local-api")
    thread.start()
    return server
