"""Examples explicitly persist snapshots; timeout/error paths do not create files."""

import contextlib
from email.message import Message
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from generated.examples import api_client, export_json


def snapshot():
    return {"schema_version": 1, "machine_id": "test", "timestamp": "2026-10-09T13:00:00Z",
            "meta": {"ready": True, "stale": False}, "cpu": {"usage_percent": 12.5}}


class Response(io.BytesIO):
    def __init__(self, value, status=200, content_type="application/json"):
        super().__init__(json.dumps(value).encode("utf-8"))
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type


class ExampleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.output = self.base / "selected.json"

    def test_library_example_starts_waits_exports_and_always_stops(self):
        lifecycle = []

        class Monitor:
            def __init__(self, **kwargs):
                lifecycle.append(("configuration", kwargs))

            def __enter__(self):
                lifecycle.append("start")
                return self

            def wait_for_snapshot(self, **kwargs):
                lifecycle.append(("wait", kwargs))
                return snapshot()

            def __exit__(self, *args):
                lifecycle.append("stop")

        with patch.object(export_json, "Monitor", Monitor), contextlib.redirect_stdout(io.StringIO()):
            status = export_json.main(["--output", str(self.output), "--interval", "0.1", "--ebpf", "disabled"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(self.output.read_text()), snapshot())
        self.assertEqual(lifecycle[0][1]["interval"], 0.1)
        self.assertEqual(lifecycle[1], "start")
        self.assertEqual(lifecycle[2][1]["min_sequence"], 2)
        self.assertEqual(lifecycle[-1], "stop")

    def test_library_example_timeout_stops_without_writing(self):
        with patch.object(export_json, "Monitor") as monitor:
            monitor.return_value.__enter__.return_value.wait_for_snapshot.return_value = None
            with contextlib.redirect_stderr(io.StringIO()):
                status = export_json.main(["--output", str(self.output), "--timeout", "0.01"])
            monitor.return_value.__exit__.assert_called_once()
        self.assertEqual(status, 1)
        self.assertFalse(self.output.exists())

    def test_http_example_validates_http_json_and_writes_selected_path(self):
        with patch.object(api_client, "urlopen", return_value=Response(snapshot())) as request, contextlib.redirect_stdout(io.StringIO()):
            status = api_client.main(["--url", "http://localhost:8000/api/metrics", "--output", str(self.output), "--timeout", "2"])
        self.assertEqual(status, 0)
        request.assert_called_once_with("http://localhost:8000/api/metrics", timeout=2.0)
        self.assertEqual(json.loads(self.output.read_text()), snapshot())

    def test_http_client_rejects_bad_status_content_type_and_schema(self):
        invalid = (
            ({}, 201, "application/json"), (snapshot(), 200, "text/plain"),
            ([], 200, "application/json"), ({**snapshot(), "schema_version": 2}, 200, "application/json"),
            ({**snapshot(), "schema_version": True}, 200, "application/json"),
            ({**snapshot(), "meta": {"ready": False}}, 200, "application/json"),
            ({**snapshot(), "machine_id": ""}, 200, "application/json"),
        )
        for value, status, content_type in invalid:
            with self.subTest(value=value, status=status, content_type=content_type):
                with patch.object(api_client, "urlopen", return_value=Response(value, status, content_type)), self.assertRaises(ValueError):
                    api_client.fetch_snapshot("http://localhost/api/metrics")
        with self.assertRaises(ValueError):
            api_client.fetch_snapshot("file:///tmp/metrics.json")

    def test_http_example_handles_connection_http_timeout_and_write_errors(self):
        errors = (
            URLError("connection refused"), TimeoutError("timed out"),
            HTTPError("http://localhost", 503, "Unavailable", {}, None),
        )
        for error in errors:
            with self.subTest(error=error), patch.object(api_client, "urlopen", side_effect=error), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(api_client.main(["--output", str(self.output)]), 1)
                self.assertFalse(self.output.exists())
        with patch.object(api_client, "urlopen", return_value=Response(snapshot())), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(api_client.main(["--output", str(self.base / "missing" / "metrics.json")]), 1)

    def test_examples_run_help_outside_repository_without_installation(self):
        for module in (export_json, api_client):
            command = [sys.executable, module.__file__, "--help"]
            result = subprocess.run(command, cwd=self.base, capture_output=True, text=True, timeout=5)
            with self.subTest(module=module.__name__):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--output", result.stdout)


if __name__ == "__main__":
    unittest.main()
