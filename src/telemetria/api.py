"""HTTP apenas consulta bytes já publicados; requisições não coletam hardware.

GET /api/metrics -> snapshot JSON
GET /metrics     -> mesmo snapshot no formato texto do Prometheus (para o Grafana)
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

from .prometheus import CONTENT_TYPE as PROMETHEUS_CONTENT_TYPE

JSON_CONTENT_TYPE = "application/json; charset=utf-8"


def servidor(monitor, host="127.0.0.1", port=9108):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            content_type = JSON_CONTENT_TYPE
            if path == "/api/metrics":
                body = monitor.snapshot()
                status = 200 if body is not None else 503
                if body is None:
                    body = b'{"status":"pending","error":"Primeira coleta em andamento"}'
            elif path == "/metrics":
                body = monitor.metrics()
                status = 200 if body is not None else 503
                content_type = PROMETHEUS_CONTENT_TYPE
                if body is None:
                    body = b"# Primeira coleta em andamento\n"
            else:
                status, body = 404, b'{"error":"Endpoint inexistente"}'
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            # O Prometheus consulta a cada poucos segundos; evita poluir o journal.
            pass

    return HTTPServer((host, port), Handler)
