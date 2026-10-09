"""Read-only HTTP access to an existing Monitor's atomic in-memory snapshot."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .configuration import DEFAULT_HOST, DEFAULT_PORT
from .configuration import SNAPSHOT_CATEGORIES as CATEGORIES

if TYPE_CHECKING:
    from .monitor import Monitor

LOG = logging.getLogger("monix.api")
ENVELOPE = (
    "schema_version", "machine_id", "hostname", "timestamp",
    "collection_interval_seconds", "sequence", "meta",
)


def handler_for(monitor: "Monitor", prometheus_enabled: bool = False) -> type[BaseHTTPRequestHandler]:
    """All routes read the same monitor; handlers never start collectors."""
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value: object) -> None:
            self._send(status, json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path in ("/health", "/healthz"):
                health = monitor.get_health()
                healthy = health["status"] == "healthy"
                self._json(200 if healthy else 503, health)
            elif path == "/metrics" and prometheus_enabled:
                payload, ready, fresh = monitor.prometheus_snapshot()
                available = ready and fresh
                self._send(200 if available else 503,
                           payload if available else b"Snapshot de metricas indisponivel ou expirado.\n",
                           "text/plain; version=0.0.4; charset=utf-8")
            elif path == "/api/metrics" or path in tuple(f"/api/metrics/{name}" for name in CATEGORIES):
                snapshot = monitor.get_snapshot()
                if path != "/api/metrics":
                    category = path.rsplit("/", 1)[-1]
                    snapshot = {
                        **{name: snapshot[name] for name in ENVELOPE},
                        "availability": {category: snapshot["availability"][category]},
                        category: snapshot[category],
                    }
                self._json(200 if snapshot["sequence"] > 0 else 503, snapshot)
            else:
                self._json(404, {"error": "Endpoint desconhecido."})

        def _read_only(self) -> None:
            self.send_response(405)
            self.send_header("Allow", "GET")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = _read_only

        def log_message(self, fmt: str, *args: object) -> None:
            LOG.debug(fmt, *args)

    return Handler


def create_server(monitor: "Monitor", host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                  prometheus_enabled: bool = False) -> ThreadingHTTPServer:
    """Create a server without starting a second collection worker."""
    return ThreadingHTTPServer((host, port), handler_for(monitor, prometheus_enabled))
