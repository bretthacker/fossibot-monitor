# fossibot/http_server.py
# Lightweight HTTP server (stdlib only) exposing device telemetry as JSON.
#
# Endpoints:
#   GET /           - full telemetry with units and register numbers
#   GET /flat       - flat key->value dict (simpler for scripts)
#   GET /status     - connection status only
#   GET /health     - 200 OK if service is running, 503 if device disconnected

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import config
from state import device_state

log = logging.getLogger(__name__)

DASHBOARD_PATH = os.path.join(os.path.dirname(__file__), "dashboard.html")


class FossibotHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"

        if path == "/" or path == "/dashboard" or path == "/index.html":
            try:
                # Prefer index.html if it exists, fall back to dashboard.html
                for name in ("index.html", "dashboard.html"):
                    p = os.path.join(os.path.dirname(__file__), name)
                    if os.path.exists(p):
                        with open(p, "rb") as f:
                            body = f.read()
                        self._send(200, body, "text/html; charset=utf-8")
                        return
                self._send(404, b"No dashboard HTML found\n", "text/plain")
            except OSError as e:
                self._send(500, str(e).encode(), "text/plain")

        elif path == "/api":
            self._json_response(device_state.as_dict())

        elif path == "/flat":
            self._json_response(device_state.as_flat_dict())

        elif path == "/status":
            age = device_state.seconds_since_update()
            self._json_response({
                "connected": device_state.is_connected(),
                "seconds_since_update": round(age, 1) if age is not None else None,
            })

        elif path == "/health":
            if device_state.is_connected():
                self._send(200, b"OK\n", "text/plain")
            else:
                self._send(503, b"Device not connected\n", "text/plain")

        else:
            self._send(404, b"Not found\n", "text/plain")

    def _json_response(self, data: dict):
        body = json.dumps(data, indent=2).encode("utf-8")
        self._send(200, body, "application/json")

    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Suppress per-request logging to keep console clean
        # Comment this out if you want HTTP access logs
        pass


class HttpServer:
    def __init__(self):
        self._thread = None
        self._server = None

    def start(self):
        self._server = HTTPServer(("", config.HTTP_PORT), FossibotHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="http-server"
        )
        self._thread.start()
        log.info(f"HTTP server listening on port {config.HTTP_PORT}")
        log.info(f"  Dashboard:      http://<pi-ip>:{config.HTTP_PORT}/")
        log.info(f"  Full telemetry: http://<pi-ip>:{config.HTTP_PORT}/api")
        log.info(f"  Flat values:    http://<pi-ip>:{config.HTTP_PORT}/flat")
        log.info(f"  Status:         http://<pi-ip>:{config.HTTP_PORT}/status")
        log.info(f"  Health check:   http://<pi-ip>:{config.HTTP_PORT}/health")

    def stop(self):
        if self._server:
            self._server.shutdown()