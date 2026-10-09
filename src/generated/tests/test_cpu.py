import json
from pathlib import Path
import queue
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from generated.collectors.cpu.collector import CpuCollector
from generated.common.paths import SystemPaths


class FakeStream:
    """Blocking pipe that lets the real reader threads run without a kernel."""

    def __init__(self):
        self.items = queue.Queue()
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        item = self.items.get(timeout=5)
        if item is None:
            raise StopIteration
        return item

    def write_line(self, line):
        self.items.put(line)

    def close(self):
        self.closed = True
        self.items.put(None)


class FakeProcess:
    def __init__(self):
        self.stdout = FakeStream()
        self.stderr = FakeStream()
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.exit(-15)

    def kill(self):
        self.killed = True
        self.exit(-9)

    def exit(self, code):
        self.returncode = code
        self.stdout.items.put(None)
        self.stderr.items.put(None)

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode


class CpuCollectorTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.paths = SystemPaths(proc=base / "proc", sys=base / "sys")
        self.paths.proc.mkdir()
        self.paths.sys.mkdir()

    def write_stat(self, user, idle, *, guest=0, cpus=("0",)):
        lines = [f"cpu {user} 0 0 {idle} 0 0 0 0 {guest} 0"]
        lines += [f"cpu{cpu} {user} 0 0 {idle} 0 0 0 0 {guest} 0" for cpu in cpus]
        (self.paths.proc / "stat").write_text("\n".join(lines) + "\n")

    @staticmethod
    def usage(samples):
        return {sample.labels["cpu"]: sample for sample in samples if sample.name == "monix_cpu_usage_percent"}

    def collector(self, mode="auto", interval=2):
        process = FakeProcess()
        with patch("generated.collectors.cpu.collector.subprocess.Popen", return_value=process) as popen:
            collector = CpuCollector(self.paths, mode, interval, Path("/fake/cpu_usage"))
        self.addCleanup(collector.close)
        popen.assert_called_once_with(
            ["/fake/cpu_usage", "--interval", str(interval)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        return collector, process

    def deliver(self, collector, process, payload):
        process.stdout.write_line(json.dumps(payload))
        deadline = time.monotonic() + 1
        while collector._latest is None and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertIsNotNone(collector._latest)

    def test_proc_baseline_and_guest_are_not_counted_twice(self):
        self.write_stat(100, 100, guest=50)
        collector = CpuCollector(self.paths, mode="disabled")
        self.addCleanup(collector.close)
        first = collector.collect()
        self.assertEqual(first[0].value, 0)
        self.assertEqual(self.usage(first), {})
        self.write_stat(150, 150, guest=100)
        samples = self.usage(collector.collect())
        self.assertEqual(set(samples), {"all", "0"})
        self.assertEqual(samples["all"].value, 50)
        self.assertEqual(samples["0"].labels["source"], "proc")

    def test_proc_new_cpu_needs_baseline_and_offline_cpu_disappears(self):
        self.write_stat(100, 100)
        collector = CpuCollector(self.paths, mode="disabled")
        self.addCleanup(collector.close)
        collector.collect()
        self.write_stat(125, 175, cpus=("0", "3"))
        self.assertNotIn("3", self.usage(collector.collect()))
        self.write_stat(150, 250, cpus=("3",))
        samples = self.usage(collector.collect())
        self.assertNotIn("0", samples)
        self.assertEqual(samples["3"].value, 25)

    def test_proc_counter_reset_requires_new_baseline(self):
        self.write_stat(100, 100)
        collector = CpuCollector(self.paths, mode="disabled")
        self.addCleanup(collector.close)
        collector.collect()
        self.write_stat(5, 5)
        self.assertEqual(self.usage(collector.collect()), {})
        self.write_stat(15, 15)
        self.assertEqual(self.usage(collector.collect())["all"].value, 50)

    def test_proc_decreasing_iowait_is_clamped(self):
        stat = self.paths.proc / "stat"
        stat.write_text("cpu 100 0 0 100 20 0 0 0\n")
        collector = CpuCollector(self.paths, mode="disabled")
        self.addCleanup(collector.close)
        collector.collect()
        stat.write_text("cpu 110 0 0 100 15 0 0 0\n")
        self.assertEqual(self.usage(collector.collect())["all"].value, 100)

    def test_helper_json_becomes_ebpf_samples_and_is_closed(self):
        self.write_stat(100, 100)
        collector, process = self.collector()
        self.deliver(collector, process, {"usage_percent": 25, "cores": {"0": 0, "2": 50}})
        samples = collector.collect()
        self.assertEqual(samples[0].value, 1)
        usage = self.usage(samples)
        self.assertEqual(set(usage), {"all", "0", "2"})
        self.assertEqual(usage["all"].value, 25)
        self.assertEqual(usage["all"].labels["source"], "ebpf")
        collector.close()
        self.assertTrue(process.terminated)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertTrue(all(not thread.is_alive() for thread in collector._threads))
        collector.close()  # idempotent cleanup

    def test_auto_stale_helper_uses_current_proc_delta(self):
        self.write_stat(100, 100)
        collector, process = self.collector()
        self.deliver(collector, process, {"usage_percent": 90, "cores": {"0": 90}})
        collector.collect()
        self.write_stat(120, 180)
        stale_time = collector._latest[0] + 7
        with patch("generated.collectors.cpu.collector.time.monotonic", return_value=stale_time):
            with self.assertLogs("generated.collectors.cpu.collector", level="WARNING") as logs:
                samples = collector.collect()
        self.assertIn("stale", " ".join(logs.output))
        self.assertEqual(samples[0].value, 0)
        self.assertEqual(self.usage(samples)["all"].value, 20)
        self.assertEqual(self.usage(samples)["all"].labels["source"], "proc")

    def test_required_stale_helper_raises(self):
        self.write_stat(100, 100)
        collector, process = self.collector(mode="required")
        self.deliver(collector, process, {"usage_percent": 20, "cores": {"0": 20}})
        stale_time = collector._latest[0] + 7
        with patch("generated.collectors.cpu.collector.time.monotonic", return_value=stale_time):
            with self.assertRaisesRegex(RuntimeError, "stale"):
                collector.collect()

    def test_required_pending_has_no_proc_fallback(self):
        self.write_stat(100, 100)
        collector, _ = self.collector(mode="required")
        collector.collect()
        self.write_stat(150, 150)
        samples = collector.collect()
        self.assertEqual(samples[0].value, 0)
        self.assertEqual(self.usage(samples), {})

    def test_required_start_error_is_visible(self):
        with patch("generated.collectors.cpu.collector.subprocess.Popen", side_effect=PermissionError("denied")):
            with self.assertLogs("generated.collectors.cpu.collector", level="WARNING"):
                with self.assertRaisesRegex(RuntimeError, "denied"):
                    CpuCollector(self.paths, mode="required")

    def test_auto_missing_helper_falls_back(self):
        self.write_stat(100, 100)
        with patch("generated.collectors.cpu.collector.subprocess.Popen", side_effect=FileNotFoundError("missing")):
            with self.assertLogs("generated.collectors.cpu.collector", level="WARNING"):
                collector = CpuCollector(self.paths)
        self.addCleanup(collector.close)
        collector.collect()
        self.write_stat(125, 175)
        samples = collector.collect()
        self.assertEqual(samples[0].value, 0)
        self.assertEqual(self.usage(samples)["all"].value, 25)

    def test_missing_proc_is_an_error_when_fallback_is_needed(self):
        disabled = CpuCollector(self.paths, mode="disabled")
        self.addCleanup(disabled.close)
        auto, _ = self.collector()
        for collector in (disabled, auto):
            with self.subTest(mode=collector.mode):
                with self.assertLogs("generated.collectors.cpu.collector", level="WARNING"):
                    with self.assertRaisesRegex(RuntimeError, "No CPU counters"):
                        collector.collect()

    def test_healthy_ebpf_does_not_require_proc_stat(self):
        for mode in ("auto", "required"):
            with self.subTest(mode=mode):
                collector, process = self.collector(mode=mode)
                self.deliver(collector, process, {"usage_percent": 40, "cores": {"0": 40}})
                with self.assertLogs("generated.collectors.cpu.collector", level="WARNING"):
                    samples = collector.collect()
                self.assertEqual(samples[0].value, 1)
                self.assertEqual(self.usage(samples)["all"].labels["source"], "ebpf")

    def test_helper_exit_invalidates_a_fresh_sample(self):
        self.write_stat(100, 100)
        collector, process = self.collector(mode="required")
        self.deliver(collector, process, {"usage_percent": 30, "cores": {"0": 30}})
        with self.assertLogs("generated.collectors.cpu.collector", level="WARNING"):
            process.exit(1)
            with self.assertRaisesRegex(RuntimeError, "exited with code 1|closed stdout"):
                collector.collect()

    def test_decode_rejects_corrupt_or_nonfinite_samples(self):
        invalid = [
            [],
            {"usage_percent": 0, "cores": {}},
            {"usage_percent": float("nan"), "cores": {"0": 10}},
            {"usage_percent": 101, "cores": {"0": 10}},
            {"usage_percent": True, "cores": {"0": 10}},
            {"usage_percent": 20, "cores": {"cpu0": 10}},
            {"usage_percent": 20, "cores": {"0": "10"}},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):
                    CpuCollector._decode(json.dumps(payload))

    def test_invalid_settings(self):
        for mode, interval in (("bad", 2), ("auto", 0), ("auto", float("nan")), ("auto", 86401)):
            with self.subTest(mode=mode, interval=interval):
                with self.assertRaises(ValueError):
                    CpuCollector(self.paths, mode=mode, interval=interval)


if __name__ == "__main__":
    unittest.main()
