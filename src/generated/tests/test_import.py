"""Importing the library needs neither a web stack nor live hardware."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


class LibraryImportTests(unittest.TestCase):
    def test_import_and_construction_have_no_hardware_or_http_side_effects(self):
        script = textwrap.dedent("""
            import builtins
            import subprocess
            import sys
            import threading
            from unittest.mock import patch

            original_import = builtins.__import__
            blocked = {"http", "fastapi", "uvicorn", "prometheus_client", "grafana", "wsgiref"}

            def isolated_import(name, *args, **kwargs):
                if name.split(".", 1)[0] in blocked:
                    raise AssertionError(f"Library imported optional web dependency {name}")
                return original_import(name, *args, **kwargs)

            with patch.object(builtins, "__import__", side_effect=isolated_import):
                with patch.object(subprocess, "Popen", side_effect=AssertionError("spawned a process")):
                    with patch.object(threading.Thread, "start", side_effect=AssertionError("started a thread")):
                        from generated import Monitor
                        monitor = Monitor(interval=5, ebpf="required")
                        snapshot = monitor.get_snapshot()
                        assert snapshot["timestamp"] is None, snapshot
                        assert "generated.api" not in sys.modules
                        assert "generated.agent" not in sys.modules
                        assert "generated.exporter" not in sys.modules
                        monitor.stop()
        """)
        source_root = str(Path(__file__).resolve().parents[2])
        environment = os.environ.copy()
        environment["PYTHONPATH"] = source_root
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        with tempfile.TemporaryDirectory() as outside_repository:
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=outside_repository,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_sources_and_default_configuration_are_package_resources(self):
        from importlib.resources import files

        package = files("generated")
        self.assertTrue(package.joinpath("config/agent.toml").is_file())
        self.assertTrue(package.joinpath("common/collector.mk").is_file())
        self.assertTrue(package.joinpath("collectors/cpu/cpu_usage.c").is_file())
        self.assertTrue(package.joinpath("collectors/cpu/cpu_usage.bpf.c").is_file())
        self.assertTrue(package.joinpath("collectors/cpu/Makefile").is_file())


if __name__ == "__main__":
    unittest.main()
