"""HTTP apenas consulta bytes já publicados; requisições não coletam hardware."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit


def servidor(monitor, host="127.0.0.1", port=9108):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if urlsplit(self.path).path != "/api/metrics":
                status, body = 404, b'{"error":"Endpoint inexistente"}'
            else:
                body = monitor.snapshot()
                status = 200 if body is not None else 503
                if body is None:
                    body = b'{"status":"pending","error":"Primeira coleta em andamento"}'
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return HTTPServer((host, port), Handler)
