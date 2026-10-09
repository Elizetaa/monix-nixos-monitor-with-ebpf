"""CPU utilization: scheduler eBPF counters, with an explicit proc fallback."""
from __future__ import annotations

from collections import deque
import json
import logging
import math
from pathlib import Path
import subprocess
import threading
import time
from typing import Any

from ...common.metrics import Sample
from ...common.paths import SystemPaths, read_text

LOGGER = logging.getLogger(__name__)


class CpuCollector:
    """Read asynchronous helper samples without blocking Prometheus scrapes.

    `required` raises when eBPF cannot run, `auto` falls back to /proc/stat,
    and `disabled` always uses /proc/stat. New counters need a baseline;
    the first scrape may therefore contain only the attachment indicator.
    """

    def __init__(
        self,
        paths: SystemPaths,
        mode: str = "auto",
        interval: float = 2.0,
        helper: Path | None = None,
    ) -> None:
        if mode not in {"auto", "required", "disabled"}:
            raise ValueError("CPU eBPF mode must be auto, required, or disabled")
        if not math.isfinite(interval) or interval <= 0 or interval > 86400:
            raise ValueError("CPU interval must be finite, > 0, and <= 86400 seconds")
        self.paths = paths
        self.mode = mode
        self.interval = interval
        self.helper = helper or Path(__file__).resolve().parents[2] / "build" / "cpu_usage"
        self._process: subprocess.Popen[str] | None = None
        self._threads: list[threading.Thread] = []
        self._lock = threading.Lock()
        self._closed = False
        self._latest: tuple[float, dict[str, Any]] | None = None
        self._failure: str | None = None
        self._stderr: deque[str] = deque(maxlen=20)
        self._previous_proc: dict[str, tuple[int, int]] = {}
        self._last_proc_error: str | None = None
        self._stale_logged = False
        self._started = time.monotonic()
        if mode != "disabled":
            self._start()

    def _fail(self, message: str) -> None:
        with self._lock:
            changed = self._failure != message
            self._failure = message
        if changed:
            LOGGER.warning("CPU eBPF unavailable: %s", message)

    def _start(self) -> None:
        try:
            self._process = subprocess.Popen(
                [str(self.helper), "--interval", str(self.interval)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            self._fail(f"cannot start {self.helper}: {exc}")
            if self.mode == "required":
                raise RuntimeError(self._failure) from exc
            return
        for target, name in (
            (self._read_stderr, "monix-cpu-stderr"),
            (self._read_stdout, "monix-cpu-stdout"),
        ):
            thread = threading.Thread(target=target, name=name, daemon=True)
            self._threads.append(thread)
            thread.start()

    @staticmethod
    def _decode(line: str) -> dict[str, Any]:
        payload = json.loads(line)
        if not isinstance(payload, dict) or not isinstance(payload.get("cores"), dict):
            raise ValueError("expected usage_percent and cores JSON object")

        def percent(value: Any) -> float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("CPU usage must be numeric")
            number = float(value)
            if not math.isfinite(number) or not 0 <= number <= 100:
                raise ValueError("CPU usage must be finite and between 0 and 100")
            return number

        total = percent(payload.get("usage_percent"))
        cores: dict[str, float] = {}
        for cpu, usage in payload["cores"].items():
            if not isinstance(cpu, str) or not cpu.isascii() or not cpu.isdecimal():
                raise ValueError("CPU identifiers must be decimal strings")
            cores[cpu] = percent(usage)
        if not cores:
            raise ValueError("CPU sample has no measured cores")
        return {"usage_percent": total, "cores": cores}

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                if self._closed:
                    return
                try:
                    payload = self._decode(line)
                except (ValueError, TypeError) as exc:
                    LOGGER.warning("Invalid CPU eBPF helper sample: %s", exc)
                    continue
                with self._lock:
                    self._latest = (time.monotonic(), payload)
                    self._stale_logged = False
            if not self._closed:
                # EOF without exit is also a failed streaming helper. Do not block
                # this reader waiting for a broken helper to eventually exit.
                code = process.poll()
                with self._lock:
                    details = " | ".join(self._stderr)
                self._fail(f"helper closed stdout (exit={code}); {details}".rstrip("; "))
        except (OSError, UnicodeError) as exc:
            if not self._closed:
                self._fail(f"cannot read helper stdout: {exc}")

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for line in process.stderr:
                message = line.strip()
                if message:
                    with self._lock:
                        self._stderr.append(message)
                    LOGGER.warning("CPU eBPF helper: %s", message)
        except (OSError, UnicodeError) as exc:
            if not self._closed:
                LOGGER.warning("Cannot read CPU eBPF helper stderr: %s", exc)

    def _proc_samples(self) -> list[Sample]:
        counters: dict[str, tuple[int, int]] = {}
        for line in read_text(self.paths.proc / "stat").splitlines():
            fields = line.split()
            if not fields or not (
                fields[0] == "cpu"
                or fields[0].startswith("cpu") and fields[0][3:].isascii() and fields[0][3:].isdecimal()
            ):
                continue
            try:
                values = [int(field) for field in fields[1:9]]
                if len(values) < 4 or any(value < 0 for value in values):
                    continue
                # guest/guest_nice are already included in user/nice. Counting
                # fields after steal duplicates guest time and lowers real usage.
                total = sum(values)
                idle = values[3] + (values[4] if len(values) > 4 else 0)
                counters["all" if fields[0] == "cpu" else fields[0][3:]] = (total, idle)
            except ValueError:
                continue
        if not counters:
            error = f"No CPU counters found in {self.paths.proc / 'stat'}"
            if error != self._last_proc_error:
                LOGGER.warning(error)
            self._last_proc_error = error
            self._previous_proc = {}
            raise RuntimeError(error)
        else:
            self._last_proc_error = None
        previous = self._previous_proc
        self._previous_proc = counters
        samples = []
        for cpu, (total, idle) in counters.items():
            if cpu not in previous:
                continue
            old_total, old_idle = previous[cpu]
            total_delta, idle_delta = total - old_total, idle - old_idle
            # Counter resets / hotplug need a new baseline. Linux permits
            # iowait to decrease; clamp it while preserving a valid total.
            if total_delta <= 0 or total - idle < old_total - old_idle:
                continue
            idle_delta = min(total_delta, max(0, idle_delta))
            usage = 100.0 * (total_delta - idle_delta) / total_delta
            samples.append(self._usage(cpu, usage, "proc"))
        return samples

    @staticmethod
    def _usage(cpu: str, value: float, source: str) -> Sample:
        return Sample(
            "monix_cpu_usage_percent",
            "CPU utilization during the last measured interval, in percent.",
            value,
            {"cpu": cpu, "source": source},
        )

    def collect(self) -> list[Sample]:
        if self._closed:
            raise RuntimeError("CPU collector is closed")
        proc_error: RuntimeError | None = None
        try:
            proc_samples = self._proc_samples()
        except RuntimeError as exc:
            proc_error = exc
            proc_samples = []
        attached = False
        samples = proc_samples
        using_proc = self.mode in {"auto", "disabled"}
        if self.mode != "disabled":
            process = self._process
            if process is not None and process.poll() is not None:
                self._fail(f"helper exited with code {process.returncode}")
            with self._lock:
                latest, failure = self._latest, self._failure
            now = time.monotonic()
            timeout = max(1.0, self.interval * 3)
            fresh = latest is not None and now - latest[0] <= timeout
            if fresh and not failure:
                payload = latest[1]
                samples = [self._usage("all", payload["usage_percent"], "ebpf")]
                samples.extend(self._usage(cpu, usage, "ebpf") for cpu, usage in payload["cores"].items())
                attached = True
                using_proc = False
            else:
                timed_out = now - self._started > timeout
                reason = failure or ("helper samples are stale" if latest else "helper has not produced a sample")
                if self.mode == "required":
                    if failure or timed_out:
                        raise RuntimeError(f"CPU eBPF required: {reason}")
                    samples = []
                elif timed_out and not failure and not self._stale_logged:
                    LOGGER.warning("CPU eBPF unavailable: %s; using /proc/stat", reason)
                    self._stale_logged = True
        if using_proc and proc_error is not None:
            raise proc_error
        return [
            Sample(
                "monix_cpu_ebpf_attached",
                "1 when a running eBPF helper has a fresh CPU sample, otherwise 0.",
                float(attached),
            ),
            *samples,
        ]

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is not None:
            if process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            for thread in self._threads:
                thread.join(timeout=1)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
