"""Exercise HTTP contract with a deterministic in-memory monitor, never sensors."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import http.client
import json
import threading
import unittest

from generated.api import CATEGORIES, create_server, handler_for


class SnapshotMonitor:
    def __init__(self):
        self.ready = False
        self.fresh = False
        self.running = True
        self.error = None
        self.collect_calls = 0

    def collect(self):
        self.collect_calls += 1
        raise AssertionError("HTTP handlers must never collect")

    def get_snapshot(self):
        data = {
            "schema_version": 1, "machine_id": "test-machine", "hostname": "test-host",
            "timestamp": "2026-10-09T13:00:00Z" if self.ready else None,
            "collection_interval_seconds": 5, "sequence": 1 if self.ready else 0,
            "availability": {name: {"status": "available" if self.ready else "unavailable",
                                    "available": self.ready, "error": None, "source": "test"}
                             for name in CATEGORIES},
            "cpu": {"usage_percent": 25.0 if self.ready else None},
            "gpu": [], "memory": {"total_bytes": 4096}, "storage": [],
            "motherboard": [], "thermal": {}, "ambient_estimate_celsius": None,
            "meta": {"ready": self.ready, "stale": self.ready and not self.fresh,
                     "running": self.running, "age_seconds": 1 if self.ready else None,
                     "stale_after_seconds": 15},
        }
        if self.error:
            data["availability"]["gpu"].update(status="error", available=False, error=self.error)
        return deepcopy(data)

    def get_health(self):
        status = ("stopped" if not self.running else "starting" if not self.ready
                  else "stale" if not self.fresh else "degraded" if self.error else "healthy")
        return {"status": status, "ready": self.ready, "fresh": self.fresh, "running": self.running,
                "stale": self.ready and not self.fresh, "error": self.error,
                "machine_id": "test-machine", "collectors": {"gpu": {"error": self.error}}}

    def prometheus_snapshot(self):
        return b"example_metric 25\n", self.ready, self.fresh


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.monitor = SnapshotMonitor()
        try:
            self.server = create_server(self.monitor, "127.0.0.1", 0, prometheus_enabled=True)
        except PermissionError as error:
            self.skipTest(f"Sandbox denies a local HTTP listening socket: {error}")
        self.worker = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.worker.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.worker.join(timeout=2)
        self.server.server_close()

    def request(self, path, method="GET"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=2)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            body = response.read()
            self.assertEqual(int(response.getheader("Content-Length")), len(body))
            return response.status, dict(response.getheaders()), body
        finally:
            connection.close()

    def make_ready(self):
        self.monitor.ready = self.monitor.fresh = True

    def test_pre_first_collection_is_503_with_explicit_metadata(self):
        for path in ("/api/metrics", "/api/metrics/cpu", "/health", "/healthz", "/metrics"):
            with self.subTest(path=path):
                status, _, body = self.request(path)
                self.assertEqual(status, 503)
                if path.startswith("/api/"):
                    snapshot = json.loads(body)
                    self.assertIsNone(snapshot["timestamp"])
                    self.assertFalse(snapshot["meta"]["ready"])
        self.assertEqual(self.monitor.collect_calls, 0)

    def test_json_and_prometheus_share_same_state_and_do_not_collect(self):
        self.make_ready()
        status, headers, body = self.request("/api/metrics?ignored=yes")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(json.loads(body)["cpu"]["usage_percent"], 25.0)
        status, headers, body = self.request("/metrics")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/plain; version=0.0.4; charset=utf-8")
        self.assertEqual(body, b"example_metric 25\n")
        for path in ("/health", "/healthz"):
            self.assertEqual(self.request(path)[0], 200)
        self.assertEqual(self.monitor.collect_calls, 0)

    def test_completed_collection_without_readings_is_still_queryable(self):
        original = self.monitor.get_snapshot
        def published_without_readings():
            snapshot = original()
            snapshot["sequence"] = 1
            snapshot["timestamp"] = "2026-10-09T13:00:00Z"
            return snapshot
        self.monitor.get_snapshot = published_without_readings
        status, _, body = self.request("/api/metrics")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["meta"]["ready"])
        self.assertEqual(self.request("/health")[0], 503)
        self.assertEqual(self.request("/metrics")[0], 503)
        self.assertEqual(self.monitor.collect_calls, 0)

    def test_category_endpoint_keeps_identity_availability_and_freshness(self):
        self.make_ready()
        for category in CATEGORIES:
            status, _, body = self.request(f"/api/metrics/{category}")
            self.assertEqual(status, 200)
            data = json.loads(body)
            self.assertEqual(data["machine_id"], "test-machine")
            self.assertIn(category, data)
            self.assertEqual(set(data["availability"]), {category})
            self.assertTrue(data["meta"]["ready"])
            for other in set(CATEGORIES) - {category}:
                self.assertNotIn(other, data)

    def test_partial_error_keeps_json_200_but_marks_unhealthy(self):
        self.make_ready()
        self.monitor.error = "sensor failed"
        status, _, body = self.request("/api/metrics")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["availability"]["gpu"]["status"], "error")
        self.assertEqual(self.request("/health")[0], 503)
        self.assertEqual(self.request("/metrics")[0], 200)

    def test_stale_json_remains_readable_and_prometheus_returns_503(self):
        self.make_ready()
        self.monitor.fresh = False
        status, _, body = self.request("/api/metrics")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["meta"]["stale"])
        self.assertEqual(self.request("/health")[0], 503)
        status, _, body = self.request("/metrics")
        self.assertEqual(status, 503)
        self.assertNotIn(b"example_metric", body)

    def test_stopped_monitor_is_unhealthy(self):
        self.make_ready()
        self.monitor.running = False
        self.assertEqual(self.request("/health")[0], 503)

    def test_concurrent_queries_only_read_one_snapshot(self):
        self.make_ready()
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(self.request, ["/api/metrics"] * 24))
        self.assertTrue(all(status == 200 for status, _, body in results))
        self.assertTrue(all(json.loads(body)["sequence"] == 1 for _, _, body in results))
        self.assertEqual(self.monitor.collect_calls, 0)

    def test_unknown_endpoints_and_control_methods_are_not_supported(self):
        for path in ("/unknown", "/api/metrics/unknown", "/api/metrics/", "/api/metrics/cpu/extra"):
            self.assertEqual(self.request(path)[0], 404)
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
            status, headers, body = self.request("/api/metrics", method)
            self.assertEqual(status, 405)
            self.assertEqual(headers["Allow"], "GET")
            self.assertEqual(body, b"")

    def test_prometheus_can_be_disabled(self):
        self.make_ready()
        self.server.RequestHandlerClass = handler_for(self.monitor)
        self.assertEqual(self.request("/metrics")[0], 404)
        self.assertEqual(self.request("/api/metrics")[0], 200)


if __name__ == "__main__":
    unittest.main()
