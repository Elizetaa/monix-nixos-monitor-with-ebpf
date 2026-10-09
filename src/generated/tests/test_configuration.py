"""TOML/CLI validation, precedence and safe defaults without host dependencies."""

import argparse
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from generated.agent import arguments, main, positive_interval
from generated.configuration import AgentConfig, load_config, validate_interval, validate_port


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "agent.toml"

    def write(self, text):
        self.path.write_text(text, encoding="utf-8")
        return self.path

    def test_defaults_need_no_file_or_prometheus(self):
        config = load_config()
        self.assertEqual(config.interval_seconds, 5)
        self.assertEqual((config.host, config.port), ("127.0.0.1", 9108))
        self.assertIsNone(config.machine_id)
        self.assertFalse(config.prometheus_enabled)

    def test_toml_values_and_relative_helper(self):
        config = load_config(self.write(
            '[monitor]\ninterval_seconds = 0.2\nmachine_id = "lab-01"\n'
            'ebpf = "disabled"\ncpu_helper = "build/cpu_usage"\n'
            'ambient_sensors = ["nct6798:temp1"]\ncollectors = ["cpu", "memory"]\n'
            '[api]\nhost = "192.168.0.8"\nport = 8000\n[prometheus]\nenabled = true\n'
        ))
        self.assertEqual(config.interval_seconds, 0.2)
        self.assertEqual(config.cpu_helper, self.path.parent / "build/cpu_usage")
        self.assertEqual(config.collectors, ("cpu", "memory"))
        self.assertEqual(config.ambient_sensors, ("nct6798:temp1",))
        self.assertTrue(config.prometheus_enabled)

    def test_interval_type_and_bounds(self):
        self.assertEqual(validate_interval(0.01), 0.01)
        self.assertEqual(validate_interval(86400), 86400)
        for value in (True, False, "5", None, 0, -1, 86401, float("nan"), float("inf"), 10 ** 1000):
            with self.subTest(value=str(value)), self.assertRaises(ValueError):
                validate_interval(value)

    def test_port_type_and_bounds(self):
        for value in (True, False, "8000", 8000.0, 0, -1, 65536):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_port(value)
        self.assertEqual(validate_port(65535), 65535)

    def test_rejects_unknown_keys_and_inconsistent_types(self):
        invalid_files = (
            '[monitor]\ninterval_seconds = true\n', '[api]\nport = "8000"\n',
            '[api]\nhost = false\n', '[monitor]\nmachine_id = ""\n',
            '[monitor]\nebpf = "maybe"\n', '[monitor]\nambient_sensors = "chip:temp1"\n',
            '[monitor]\nambient_sensors = ["unlabelled"]\n',
            '[monitor]\ncollectors = ["memory", "memory"]\n',
            '[monitor]\ncollectors = ["unknown"]\n', '[prometheus]\nenabled = 1\n',
            '[api]\nprt = 8000\n', '[unknown]\nfoo = "bar"\n', 'monitor = 5\n',
        )
        for data in invalid_files:
            with self.subTest(data=data), self.assertRaises(ValueError):
                load_config(self.write(data))

    def test_cli_overrides_selected_file_and_preserves_other_fields(self):
        path = self.write('[monitor]\ninterval_seconds=9\nmachine_id="lab"\n'
                          'collectors=["memory"]\n[api]\nport=8000\n[prometheus]\nenabled=true\n')
        args = arguments(["--config", str(path), "--interval", "0.25", "--port", "9000", "--no-prometheus"])
        self.assertEqual(args.configuration.interval_seconds, 0.25)
        self.assertEqual(args.configuration.port, 9000)
        self.assertEqual(args.configuration.machine_id, "lab")
        self.assertEqual(args.configuration.collectors, ("memory",))
        self.assertFalse(args.configuration.prometheus_enabled)
        args = arguments(["--config", str(path), "--collector", "all"])
        self.assertIsNone(args.configuration.collectors)

    def test_legacy_defaults_only_enable_optional_prometheus(self):
        config = arguments([], legacy=True).configuration
        self.assertTrue(config.prometheus_enabled)
        self.assertEqual(config.interval_seconds, AgentConfig().interval_seconds)
        self.assertFalse(arguments(["--no-prometheus"], legacy=True).configuration.prometheus_enabled)

    def test_cli_rejects_invalid_values_and_bad_config(self):
        for flags in (["--port", "0"], ["--port", "65536"], ["--interval", "nan"],
                      ["--interval", "bad"], ["--machine-id", ""], ["--host", ""],
                      ["--config", str(self.path)]):
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                arguments(flags)
            self.assertEqual(caught.exception.code, 2)
        with self.assertRaises(argparse.ArgumentTypeError):
            positive_interval("inf")

    def test_cli_reports_failed_cleanup_with_nonzero_exit(self):
        with patch("generated.Monitor") as monitor, patch("generated.agent._wait_once", return_value={"availability": {}}):
            monitor.return_value.stop.side_effect = RuntimeError("worker did not stop")
            with contextlib.redirect_stdout(io.StringIO()), self.assertLogs("monix", level="ERROR"):
                self.assertEqual(main(["--once", "--collector", "memory"]), 1)
            monitor.return_value.start.assert_called_once()
            monitor.return_value.stop.assert_called_once_with(timeout=5.0)


if __name__ == "__main__":
    unittest.main()
