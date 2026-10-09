"""Lifecycle, scheduling, atomic snapshots and isolation without hardware."""

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from generated import Monitor
from generated.common.metrics import Sample
from generated.common.paths import SystemPaths


def memory(value=100):
    return [Sample("monix_memory_total_bytes", "RAM", value),
            Sample("monix_memory_used_bytes", "RAM", value / 2),
            Sample("monix_memory_available_bytes", "RAM", value / 2),
            Sample("monix_memory_free_bytes", "RAM", value / 4)]


class ScriptedCollector:
    def __init__(self, *results):
        self.results = results or (memory(),)
        self.calls = 0
        self.closed = 0

    def collect(self):
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        self.closed += 1


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = SystemPaths(self.root / "proc", self.root / "sys")
        self.paths.proc.mkdir()
        self.paths.sys.mkdir()

    def monitor(self, collectors=None, **kwargs):
        monitor = Monitor(machine_id="lab-01", hostname="test-host", paths=self.paths,
                          collectors=collectors if collectors is not None else {"memory": ScriptedCollector()}, **kwargs)
        self.addCleanup(monitor.stop)
        return monitor

    def test_before_first_collection_is_explicit_and_json_serializable(self):
        monitor = self.monitor()
        snapshot = monitor.get_snapshot()
        self.assertIsNone(snapshot["timestamp"])
        self.assertIsNone(snapshot["cpu"]["usage_percent"])
        self.assertIsNone(snapshot["cpu"]["ebpf_attached"])
        self.assertFalse(snapshot["meta"]["ready"])
        self.assertFalse(snapshot["meta"]["running"])
        self.assertEqual(snapshot["sequence"], 0)
        self.assertIn("unmapped_partitions", snapshot)
        json.dumps(snapshot, allow_nan=False)
        self.assertIsNone(monitor.wait_for_snapshot(timeout=0))

    def test_construction_does_not_initialize_native_collectors(self):
        with patch("generated.monitor.CpuCollector") as cpu:
            monitor = Monitor(paths=self.paths, machine_id="lab-01")
            monitor.get_snapshot()
            monitor.stop()
            cpu.assert_not_called()

    def test_start_is_idempotent_and_stop_closes_once(self):
        collector = ScriptedCollector()
        monitor = self.monitor({"memory": collector}, interval=10)
        self.assertIs(monitor.start(), monitor)
        snapshot = monitor.wait_for_snapshot(timeout=1)
        self.assertIsNotNone(snapshot)
        thread = monitor._thread
        monitor.start()
        self.assertIs(thread, monitor._thread)
        self.assertEqual(collector.calls, 1)
        started = time.monotonic()
        monitor.stop()
        self.assertLess(time.monotonic() - started, 0.5)
        monitor.stop()
        self.assertEqual(collector.closed, 1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(monitor.get_health()["status"], "stopped")

    def test_context_manager_cleans_up_and_can_restart(self):
        collector = ScriptedCollector()
        monitor = self.monitor({"memory": collector}, interval=10)
        with monitor:
            self.assertTrue(monitor.wait_for_snapshot(timeout=1)["meta"]["running"])
        with monitor:
            self.assertEqual(monitor.wait_for_snapshot(timeout=1)["sequence"], 1)
        self.assertEqual(collector.closed, 2)

    def test_queries_are_copies_and_do_not_collect_or_read_hardware(self):
        collector = ScriptedCollector()
        monitor = self.monitor({"memory": collector}, interval=10).start()
        monitor.wait_for_snapshot(timeout=1)
        with patch("generated.monitor.normalize", side_effect=AssertionError("read triggered collection")):
            for _ in range(30):
                snapshot = monitor.get_snapshot()
                snapshot["memory"]["total_bytes"] = 0
                snapshot["collectors"]["memory"]["success"] = False
                monitor.get_snapshot("cpu")["cores"].append({"cpu": "fake"})
                monitor.get_health()
                monitor.prometheus_snapshot()
        self.assertEqual(collector.calls, 1)
        self.assertEqual(monitor.get_snapshot("memory")["total_bytes"], 100)
        self.assertEqual(monitor.get_snapshot("cpu")["cores"], [])
        self.assertTrue(monitor.get_health()["collectors"]["memory"]["success"])

    def test_partial_or_absent_sensors_keep_other_categories(self):
        monitor = self.monitor({"memory": ScriptedCollector(), "gpu": ScriptedCollector([])}, interval=10).start()
        snapshot = monitor.wait_for_snapshot(timeout=1)
        self.assertTrue(snapshot["availability"]["memory"]["available"])
        self.assertEqual(snapshot["availability"]["gpu"]["status"], "unavailable")
        self.assertIsNone(snapshot["cpu"]["temperature_celsius"])
        self.assertEqual(monitor.get_health()["status"], "healthy")

    def test_failed_domain_removes_old_samples_and_preserves_last_success(self):
        flaky = ScriptedCollector([Sample("monix_gpu_usage_percent", "GPU", 50, {"gpu": "gpu0"})], RuntimeError("driver gone"))
        monitor = self.monitor({"gpu": flaky, "memory": ScriptedCollector()})
        monitor._initialize_collectors()
        monitor._collect_once()
        successful_at = monitor.get_snapshot()["collectors"]["gpu"]["last_success_timestamp"]
        with self.assertLogs("generated.monitor", level="ERROR"):
            monitor._collect_once()
        snapshot = monitor.get_snapshot()
        self.assertEqual(snapshot["gpu"], [])
        self.assertEqual(snapshot["availability"]["gpu"]["status"], "error")
        self.assertEqual(snapshot["memory"]["total_bytes"], 100)
        self.assertEqual(snapshot["collectors"]["gpu"]["last_success_timestamp"], successful_at)
        payload = monitor.prometheus_snapshot()[0]
        self.assertNotIn(b"monix_gpu_usage_percent{", payload)
        self.assertIn(b'monix_collector_success{collector="gpu",hostname="test-host"} 0', payload)
        with patch("generated.monitor.LOG.exception") as log:
            monitor._collect_once()
            log.assert_not_called()

    def test_never_successful_collector_is_reported_without_timestamp(self):
        monitor = self.monitor({"memory": ScriptedCollector(ValueError("unreadable"))})
        monitor._initialize_collectors()
        with self.assertLogs("generated.monitor", level="ERROR"):
            monitor._collect_once()
        snapshot = monitor.get_snapshot()
        self.assertFalse(snapshot["meta"]["ready"])
        self.assertIsNone(snapshot["collectors"]["memory"]["last_success_timestamp"])
        self.assertNotIn(b"monix_collector_last_success_timestamp_seconds{", monitor.prometheus_snapshot()[0])

    def test_duplicate_series_are_isolated_within_and_between_domains(self):
        duplicate = Sample("monix_memory_total_bytes", "RAM", 999)
        flaky = ScriptedCollector([duplicate, duplicate], [duplicate], [])
        monitor = self.monitor({"memory": ScriptedCollector(), "gpu": flaky})
        monitor._initialize_collectors()
        with self.assertLogs("generated.monitor", level="ERROR"):
            monitor._collect_once()
        for _ in range(2):
            self.assertEqual(monitor.get_snapshot("memory")["total_bytes"], 100)
            self.assertFalse(monitor.get_snapshot()["collectors"]["gpu"]["success"])
            if _ == 0:
                monitor._collect_once()
        monitor._collect_once()
        self.assertTrue(monitor.get_snapshot()["collectors"]["gpu"]["success"])

    def test_reserved_health_metrics_do_not_break_prometheus(self):
        monitor = self.monitor({"memory": ScriptedCollector([Sample("monix_collector_success", "bad", 1)])})
        monitor._initialize_collectors()
        with self.assertLogs("generated.monitor", level="ERROR"):
            monitor._collect_once()
        self.assertEqual(monitor.prometheus_snapshot()[0].count(b"# TYPE monix_collector_success"), 1)

    def test_snapshot_is_published_only_after_all_collectors_finish(self):
        entered, release = threading.Event(), threading.Event()
        second = ScriptedCollector(memory(200))
        first = ScriptedCollector([Sample("monix_cpu_usage_percent", "CPU", 10, {"cpu": "all"})],
                                  [Sample("monix_cpu_usage_percent", "CPU", 20, {"cpu": "all"})])
        monitor = self.monitor({"cpu": first, "memory": second})
        monitor._initialize_collectors()
        monitor._collect_once()

        class Blocking:
            def collect(self):
                entered.set()
                release.wait(2)
                return memory(400)

        monitor._collectors["memory"] = Blocking()
        worker = threading.Thread(target=monitor._collect_once)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            for _ in range(20):
                snapshot = monitor.get_snapshot()
                self.assertEqual((snapshot["cpu"]["usage_percent"], snapshot["memory"]["total_bytes"], snapshot["sequence"]), (10, 200, 1))
        finally:
            release.set()
            worker.join(2)
        snapshot = monitor.get_snapshot()
        self.assertEqual((snapshot["cpu"]["usage_percent"], snapshot["memory"]["total_bytes"], snapshot["sequence"]), (20, 400, 2))

    def test_freshness_uses_monotonic_clock_and_stale_preserves_json(self):
        monitor = self.monitor(interval=10).start()
        monitor.wait_for_snapshot(timeout=1)
        recorded = monitor._state[2]
        with patch("generated.monitor.time.monotonic", return_value=recorded + 29):
            self.assertFalse(monitor.get_snapshot()["meta"]["stale"])
        with patch("generated.monitor.time.monotonic", return_value=recorded + 31):
            snapshot = monitor.get_snapshot()
            self.assertTrue(snapshot["meta"]["stale"])
            self.assertEqual(snapshot["memory"]["total_bytes"], 100)
            self.assertEqual(monitor.get_health()["status"], "stale")
            self.assertFalse(monitor.prometheus_snapshot()[2])
        self.assertEqual(self.monitor(interval=1).stale_after_seconds, 15)

    def test_scheduler_keeps_start_intervals_and_skips_missed_slots(self):
        monitor = self.monitor(interval=5)
        waits = []
        clock = [0.0]

        class StopAfterThree:
            def is_set(self):
                return len(waits) >= 3

            def wait(self, delay):
                waits.append(delay)
                clock[0] += delay

        durations = iter((1, 7, 1))
        def collect():
            clock[0] += next(durations)

        monitor._stop_event = StopAfterThree()
        try:
            with patch("generated.monitor.time.monotonic", side_effect=lambda: clock[0]), patch.object(monitor, "_collect_once", side_effect=collect):
                monitor._run()
            self.assertEqual(waits, [4, 3, 4])
        finally:
            monitor._stop_event = threading.Event()

    def test_overrun_has_one_worker_and_no_pending_queue(self):
        calls, active, maximum = [], [0], [0]
        finished = threading.Event()

        class Slow:
            def collect(self):
                active[0] += 1
                maximum[0] = max(maximum[0], active[0])
                calls.append(time.monotonic())
                time.sleep(0.04)
                active[0] -= 1
                if len(calls) >= 3:
                    finished.set()
                return memory()

        monitor = self.monitor({"memory": Slow()}, interval=0.01).start()
        self.assertTrue(finished.wait(1))
        monitor.stop()
        self.assertEqual(maximum[0], 1)
        self.assertTrue(all(b - a >= 0.04 for a, b in zip(calls, calls[1:])))

    def test_stop_does_not_close_a_collector_while_it_is_running(self):
        entered, release = threading.Event(), threading.Event()
        closed = []

        class Blocking:
            def collect(self):
                entered.set()
                release.wait(2)
                return memory()

            def close(self):
                closed.append(True)

        monitor = self.monitor({"memory": Blocking()}, interval=10).start()
        self.assertTrue(entered.wait(1))
        try:
            with self.assertRaises(RuntimeError):
                monitor.stop(timeout=0.01)
            self.assertEqual(closed, [])
            with self.assertRaises(RuntimeError):
                monitor.start()
        finally:
            release.set()
            monitor.stop()
        self.assertEqual(closed, [True])

    def test_cleanup_error_does_not_prevent_other_cleanup(self):
        class BrokenClose(ScriptedCollector):
            def close(self):
                raise OSError("close failed")

        stable = ScriptedCollector([])
        monitor = self.monitor({"memory": BrokenClose(), "gpu": stable}, interval=10).start()
        monitor.wait_for_snapshot(timeout=1)
        with self.assertLogs("generated.monitor", level="ERROR"):
            monitor.stop()
        self.assertEqual(stable.closed, 1)

    def test_wait_requires_category_and_sequence_without_running_collector(self):
        collector = ScriptedCollector()
        monitor = self.monitor({"memory": collector}, interval=0.02).start()
        snapshot = monitor.wait_for_snapshot(timeout=1, require_categories=("memory",), min_sequence=2)
        self.assertGreaterEqual(snapshot["sequence"], 2)
        self.assertIsNone(monitor.wait_for_snapshot(timeout=0, require_categories=("gpu",)))

    def test_snapshot_replacement_has_no_history_or_disk_writes(self):
        monitor = self.monitor(interval=10)
        monitor._initialize_collectors()
        with patch("pathlib.Path.write_text", side_effect=AssertionError("metric persistence")), patch("builtins.open", side_effect=AssertionError("file writer")):
            for _ in range(30):
                monitor._collect_once()
        self.assertEqual(monitor.get_snapshot()["sequence"], 30)
        self.assertIsInstance(monitor._state, tuple)
        self.assertFalse(hasattr(monitor, "history"))
        self.assertEqual(set(self.root.rglob("*")), {self.paths.proc, self.paths.sys})

    def test_startup_error_is_isolated_and_worker_can_recover_other_data(self):
        monitor = Monitor(paths=self.paths, machine_id="lab-01", ebpf="required", categories=("cpu", "memory"), interval=10)
        self.addCleanup(monitor.stop)
        with patch("generated.monitor.CpuCollector", side_effect=RuntimeError("BPF unavailable")), patch("generated.monitor.MemoryCollector", return_value=ScriptedCollector()), self.assertLogs("generated.monitor", level="ERROR"):
            monitor.start()
            snapshot = monitor.wait_for_snapshot(timeout=1)
        self.assertEqual(snapshot["memory"]["total_bytes"], 100)
        self.assertEqual(snapshot["availability"]["cpu"]["status"], "error")
        self.assertEqual(monitor.get_health()["status"], "degraded")

    def test_normalization_failure_is_explicit_instead_of_killing_worker(self):
        monitor = self.monitor()
        monitor._initialize_collectors()
        with patch("generated.monitor.normalize", side_effect=ValueError("bad mapping")), self.assertLogs("generated.monitor", level="ERROR"):
            monitor._collect_once()
        snapshot = monitor.get_snapshot()
        self.assertFalse(snapshot["meta"]["ready"])
        self.assertIn("normalization", snapshot["collectors"])
        monitor._collect_once()
        self.assertTrue(monitor.get_snapshot()["meta"]["ready"])

    def test_invalid_configuration_and_category_are_rejected(self):
        for value in (True, 0, -1, float("nan"), float("inf"), "5", 86401, 10 ** 1000):
            with self.subTest(value=str(value)), self.assertRaises(ValueError):
                Monitor(interval=value)
        for options in ({"machine_id": ""}, {"hostname": ""}, {"ebpf": "bad"}, {"categories": ()}, {"categories": ("bad",)}, {"categories": ("cpu", "cpu")}, {"ambient_sensors": "chip:temp1"}, {"ambient_sensors": ("chip:bad",)}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                Monitor(**options)
        monitor = self.monitor()
        with self.assertRaises(ValueError):
            monitor.get_snapshot("bad")
        for options in ({"timeout": -1}, {"timeout": float("nan")}, {"min_sequence": True}, {"require_categories": ("bad",)}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                monitor.wait_for_snapshot(**options)


if __name__ == "__main__":
    unittest.main()
