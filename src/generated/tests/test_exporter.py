"""Preserved Prometheus formatting and public CLI validation regressions.

The old TelemetryExporter lifecycle cases now live in test_monitor.py and
HTTP behavior is exercised in test_api.py against the shared Monitor state.
"""

import argparse
import contextlib
import io
import unittest
from unittest.mock import patch

from generated.common.metrics import Sample, render
from generated.agent import arguments, positive_interval


class RenderTests(unittest.TestCase):
    def test_escapes_labels_and_help_without_losing_unicode(self):
        payload = render([
            Sample("monix_example", "line one\\path\nline two", 1.5,
                   {"sensor": 'placa "mãe"\\chip\nambiente'}),
        ], 'host"\\\n')
        expected = (
            '# HELP monix_example line one\\\\path\\nline two\n'
            '# TYPE monix_example gauge\n'
            'monix_example{hostname="host\\"\\\\\\n",sensor="placa \\"mãe\\"\\\\chip\\nambiente"} 1.5\n'
        )
        self.assertEqual(payload.decode("utf-8"), expected)

    def test_metric_families_and_labels_are_ordered_with_one_descriptor(self):
        payload = render([
            Sample("z_metric", "Z", 2, {"z": "last", "a": "first"}),
            Sample("a_metric", "A", 3, {"id": "one"}),
            Sample("a_metric", "A", 4, {"id": "two"}),
        ], "host").decode()
        self.assertEqual(payload.count("# HELP a_metric"), 1)
        self.assertEqual(payload.count("# TYPE a_metric"), 1)
        self.assertLess(payload.index("# HELP a_metric"), payload.index("# HELP z_metric"))
        self.assertIn('z_metric{a="first",hostname="host",z="last"} 2\n', payload)

    def test_nonfinite_values_are_omitted(self):
        payload = render([
            Sample("invalid_nan", "NaN", float("nan")),
            Sample("invalid_inf", "Infinity", float("inf")),
            Sample("invalid_negative_inf", "Infinity", -float("inf")),
            Sample("valid_metric", "Valid", 0),
        ], "host").decode()
        self.assertNotIn("invalid_", payload)
        self.assertIn('valid_metric{hostname="host"} 0\n', payload)

    def test_duplicate_series_are_rejected_independently_of_label_order(self):
        with self.assertRaises(ValueError):
            render([
                Sample("same_metric", "Value", 1, {"a": "A", "b": "B"}),
                Sample("same_metric", "Value", 2, {"b": "B", "a": "A"}),
            ], "host")

    def test_distinct_label_values_are_separate_series(self):
        payload = render([
            Sample("same_metric", "Value", 1, {"device": "sda"}),
            Sample("same_metric", "Value", 2, {"device": "sdb"}),
        ], "host").decode()
        self.assertEqual(sum(line.startswith("same_metric{") for line in payload.splitlines()), 2)

    def test_invalid_names_and_reserved_labels_are_rejected(self):
        for name in ("bad-name", "0starts_with_number", "name\nother", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                render([Sample(name, "Value", 1)], "host")
        for label in ("bad-label", "0label", "__reserved", ""):
            with self.subTest(label=label), self.assertRaises(ValueError):
                render([Sample("valid_metric", "Value", 1, {label: "value"})], "host")

    def test_hostname_is_always_the_exporter_hostname(self):
        payload = render([Sample("value", "Value", 1, {"hostname": "collector-host"})], "exporter-host").decode()
        self.assertIn('hostname="exporter-host"', payload)
        self.assertNotIn("collector-host", payload)


class ArgumentsTests(unittest.TestCase):
    def test_positive_interval_validation(self):
        self.assertEqual(positive_interval("0.5"), 0.5)
        for value in ("0", "-1", "nan", "inf", "-inf"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                positive_interval(value)

    def test_cli_rejects_invalid_ports_and_intervals(self):
        for option, value in (("--port", "0"), ("--port", "65536"), ("--interval", "nan"), ("--interval", "bad")):
            with self.subTest(option=option, value=value), patch("sys.argv", ["exporter.py", option, value]):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    arguments()
                self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
