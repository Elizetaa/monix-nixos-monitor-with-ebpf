"""Coleta periódica e publicação atômica de um único snapshot em RAM."""

from copy import deepcopy
from datetime import datetime, timezone
import logging
import math
from pathlib import Path
import socket
import threading
import time

from .collectors.cpu.collector import CpuCollector
from .collectors.gpu.collector import GpuCollector
from .collectors.memory.collector import MemoryCollector
from .collectors.storage.collector import StorageCollector
from .collectors.thermal.collector import ThermalCollector
from .common.metrics import Sample, validate_samples
from .common.paths import SystemPaths, read_text
from .configuration import DEFAULT_INTERVAL, validate_ambient_sensors, validate_interval
from .configuration import SNAPSHOT_CATEGORIES as CATEGORIES
from .normalization import normalize

LOG = logging.getLogger("generated.monitor")


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _machine_id(hostname: str) -> str:
    return read_text(Path("/etc/machine-id")) or read_text(Path("/var/lib/dbus/machine-id")) or hostname


def _empty_data() -> dict:
    return {
        "cpu": {"name": None, "model": None, "vendor": None, "logical_cores": None,
                "source": None, "usage_percent": None, "temperature_celsius": None,
                "temperatures": [], "cores": [], "ebpf_attached": None},
        "gpu": [],
        "memory": {"source": "procfs", "total_bytes": None, "used_bytes": None,
                   "available_bytes": None, "free_bytes": None, "allocated_bytes": None,
                   "usage_percent": None},
        "storage": [], "motherboard": [], "thermal": [],
        "ambient_estimate_celsius": None, "unmapped_filesystems": [], "unmapped_partitions": [],
    }


class Monitor:
    """Monitor sem I/O de rede ou persistência de métricas.

    O intervalo é entre inícios de coletas, usando relógio monotônico. Se uma
    coleta exceder o intervalo, os horários perdidos são descartados. ``start``
    cria um único worker; consultas nunca executam os coletores. ``collectors``
    e ``paths`` permitem injetar fixtures nos testes.
    """

    def __init__(self, interval: float = DEFAULT_INTERVAL, machine_id: str | None = None,
                 *, ebpf: str = "auto", cpu_helper: Path | None = None,
                 paths: SystemPaths | None = None, ambient_sensors: tuple[str, ...] = (),
                 collectors: dict | None = None, hostname: str | None = None,
                 categories: tuple[str, ...] | None = None):
        interval = validate_interval(interval)
        if machine_id is not None and (not isinstance(machine_id, str) or not machine_id.strip()):
            raise ValueError("machine_id deve ser uma string não vazia")
        if ebpf not in ("auto", "required", "disabled"):
            raise ValueError("ebpf deve ser auto, required ou disabled")
        if hostname is not None and (not isinstance(hostname, str) or not hostname.strip()):
            raise ValueError("hostname deve ser uma string não vazia")
        if categories is not None and (not isinstance(categories, (tuple, list)) or not categories or any(name not in CATEGORIES for name in categories) or len(set(categories)) != len(categories)):
            raise ValueError(f"categories deve conter categorias de {CATEGORIES}")
        self.interval = float(interval)
        self.hostname = hostname or socket.gethostname()
        self.machine_id = machine_id or _machine_id(self.hostname)
        self.paths = paths or SystemPaths()
        self.ebpf = ebpf
        self.cpu_helper = cpu_helper
        self.ambient_sensors = validate_ambient_sensors(ambient_sensors)
        self.categories = tuple(categories) if categories else None
        self.stale_after_seconds = max(15.0, self.interval * 3)
        self._provided_collectors = dict(collectors) if collectors is not None else None
        self._collectors: dict = {}
        self._startup_errors: dict[str, str] = {}
        self._last_errors: dict[str, str | None] = {}
        self._last_success: dict[str, tuple[str, float]] = {}
        self._condition = threading.Condition(threading.RLock())
        self._lifecycle = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        # JSON, amostras originais e instante monotônico são publicados juntos.
        self._state: tuple[dict, tuple[Sample, ...], float] | None = None

    def _initialize_collectors(self) -> None:
        if self._provided_collectors is not None:
            self._collectors = dict(self._provided_collectors)
            return
        factories = {
            "cpu": lambda: CpuCollector(self.paths, mode=self.ebpf, interval=self.interval, helper=self.cpu_helper),
            "gpu": lambda: GpuCollector(self.paths),
            "memory": lambda: MemoryCollector(self.paths),
            "storage": lambda: StorageCollector(self.paths),
            "thermal": lambda: ThermalCollector(self.paths, ambient_sensors=self.ambient_sensors),
        }
        selected = set(self.categories or factories)
        if "cpu" in selected or "motherboard" in selected:
            selected.add("thermal")
        for name, factory in factories.items():
            if name not in selected:
                continue
            try:
                self._collectors[name] = factory()
            except Exception as error:
                self._startup_errors[name] = f"{type(error).__name__}: {error}"
                LOG.exception("Não foi possível inicializar o coletor %s", name)

    def start(self) -> "Monitor":
        with self._lifecycle:
            if self._thread is not None and self._thread.is_alive():
                if self._stop_event.is_set():
                    raise RuntimeError("O worker anterior ainda está encerrando")
                return self
            self._close_collectors()
            self._stop_event.clear()
            self._startup_errors.clear()
            self._last_errors.clear()
            self._last_success.clear()
            self._collectors = {}
            with self._condition:
                self._state = None
            self._initialize_collectors()
            with self._condition:
                self._running = True
                self._thread = threading.Thread(target=self._run, name="monix-monitor", daemon=True)
                try:
                    self._thread.start()
                except Exception:
                    self._running = False
                    self._thread = None
                    self._close_collectors()
                    raise
        return self

    def _availability(self, data: dict, diagnostics: dict) -> dict:
        cpu, memory = data["cpu"], data["memory"]
        cpu_values = (cpu.get("usage_percent"), cpu.get("temperature_celsius"))
        gpu_values = [gpu for gpu in data["gpu"] if any(gpu.get(field) is not None for field in ("usage_percent", "temperature_celsius", "fan_percent", "fan_rpm"))]
        available = {
            "cpu": any(value is not None for value in cpu_values),
            "gpu": bool(gpu_values),
            "memory": memory.get("total_bytes") is not None,
            "storage": bool(data["storage"] or data.get("unmapped_filesystems") or data.get("unmapped_partitions")),
            "motherboard": bool(data["motherboard"]),
            "thermal": bool(data["thermal"]),
        }
        partial = {
            "cpu": not all(value is not None for value in cpu_values),
            "gpu": len(gpu_values) != len(data["gpu"]) or any(gpu.get("usage_percent") is None or gpu.get("temperature_celsius") is None for gpu in gpu_values),
            "memory": any(memory.get(field) is None for field in ("used_bytes", "available_bytes", "free_bytes")),
            "storage": bool(data.get("unmapped_filesystems") or data.get("unmapped_partitions")) or any(
                disk.get("total_bytes") is None
                or any(partition.get("total_bytes") is None or not partition.get("backing_known", True) for partition in disk["partitions"])
                or any(not filesystem.get("backing_known", True) or any(filesystem.get(field) is None for field in ("total_bytes", "used_bytes", "available_bytes")) for filesystem in disk["filesystems"])
                for disk in data["storage"]),
        }
        temperature_sources = sorted({item["source"] for item in cpu.get("temperatures", ())})
        sources = {"cpu": cpu.get("source") or ("/".join(temperature_sources) or None),
                   "gpu": "driver", "memory": "procfs", "storage": "sysfs/statvfs",
                   "motherboard": "hwmon", "thermal": "hwmon"}
        owners = {"cpu": ("cpu", "thermal"), "gpu": ("gpu",), "memory": ("memory",),
                  "storage": ("storage",), "motherboard": ("thermal",), "thermal": ("thermal",)}
        result = {}
        for category in CATEGORIES:
            errors = [diagnostics[name]["error"] for name in owners[category] if name in diagnostics and diagnostics[name]["error"]]
            if "normalization" in diagnostics:
                errors.append(diagnostics["normalization"]["error"])
            status = "partial" if available[category] and (partial.get(category, False) or errors) else "available" if available[category] else "error" if errors else "unavailable"
            result[category] = {"status": status, "available": available[category],
                                "error": "; ".join(errors) or None,
                                "source": sources[category] if available[category] else None}
        return result

    def _collect_once(self) -> None:
        samples: list[Sample] = []
        diagnostics: dict = {}
        for name in (*self._collectors, *self._startup_errors):
            started = time.monotonic()
            error_message = self._startup_errors.get(name)
            if error_message is None:
                try:
                    values = list(self._collectors[name].collect())
                    if any(sample.name.startswith("monix_collector_") for sample in values):
                        raise ValueError("O prefixo monix_collector_ é reservado ao monitor")
                    samples = validate_samples([*samples, *values], self.hostname)
                    self._last_success[name] = (_timestamp(), time.time())
                except Exception as error:
                    error_message = f"{type(error).__name__}: {error}"
                    if self._last_errors.get(name) != error_message:
                        LOG.exception("Falha no coletor %s", name)
            self._last_errors[name] = error_message
            last_success = self._last_success.get(name)
            diagnostics[name] = {"success": error_message is None, "error": error_message,
                                 "duration_seconds": time.monotonic() - started,
                                 "last_success_timestamp": last_success[0] if last_success else None}
        try:
            data = normalize(samples, self.paths)
        except Exception as error:
            message = f"{type(error).__name__}: {error}"
            if self._last_errors.get("normalization") != message:
                LOG.exception("Falha ao normalizar o snapshot")
            self._last_errors["normalization"] = message
            diagnostics["normalization"] = {"success": False, "error": message,
                                            "duration_seconds": 0, "last_success_timestamp": None}
            data, samples = _empty_data(), []
        else:
            self._last_errors.pop("normalization", None)
        availability = self._availability(data, diagnostics)
        for name, diagnostic in diagnostics.items():
            labels = {"collector": name}
            samples.extend((
                Sample("monix_collector_success", "Última coleta bem-sucedida (1/0).", int(diagnostic["success"]), labels),
                Sample("monix_collector_duration_seconds", "Duração da última coleta.", diagnostic["duration_seconds"], labels),
            ))
            if name in self._last_success:
                samples.append(Sample("monix_collector_last_success_timestamp_seconds", "Instante Unix da última coleta bem-sucedida.", self._last_success[name][1], labels))
        with self._condition:
            sequence = self._state[0]["sequence"] + 1 if self._state else 1
            snapshot = {"schema_version": 1, "machine_id": self.machine_id, "hostname": self.hostname,
                        "timestamp": _timestamp(), "collection_interval_seconds": self.interval,
                        "sequence": sequence, "availability": availability, "collectors": diagnostics, **data}
            self._state = (snapshot, tuple(samples), time.monotonic())
            self._condition.notify_all()

    def _run(self) -> None:
        deadline = time.monotonic()
        try:
            while not self._stop_event.is_set():
                self._collect_once()
                deadline += self.interval
                now = time.monotonic()
                if deadline < now:
                    deadline += (math.floor((now - deadline) / self.interval) + 1) * self.interval
                self._stop_event.wait(max(0, deadline - now))
        finally:
            with self._condition:
                self._running = False
                self._condition.notify_all()

    def _snapshot_locked(self) -> dict:
        if self._state is None:
            snapshot = {"schema_version": 1, "machine_id": self.machine_id, "hostname": self.hostname,
                        "timestamp": None, "collection_interval_seconds": self.interval, "sequence": 0,
                        "availability": {category: {"status": "unavailable", "available": False, "error": None, "source": None} for category in CATEGORIES},
                        "collectors": {}, **_empty_data()}
            age = None
        else:
            snapshot = deepcopy(self._state[0])
            age = max(0.0, time.monotonic() - self._state[2])
        snapshot["meta"] = {"has_snapshot": snapshot["sequence"] > 0,
                            "ready": any(item["available"] for item in snapshot["availability"].values()),
                            "stale": age is not None and age >= self.stale_after_seconds,
                            "running": self._running and not self._stop_event.is_set(),
                            "age_seconds": age, "stale_after_seconds": self.stale_after_seconds}
        return snapshot

    def get_snapshot(self, category: str | None = None) -> dict | list:
        if category is not None and category not in CATEGORIES:
            raise ValueError(f"Categoria desconhecida: {category}")
        with self._condition:
            snapshot = self._snapshot_locked()
        return snapshot[category] if category is not None else snapshot

    def wait_for_snapshot(self, timeout: float = 10.0, *, require_categories: tuple[str, ...] = (), min_sequence: int = 1) -> dict | None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout deve ser finito e não negativo")
        if isinstance(min_sequence, bool) or not isinstance(min_sequence, int) or min_sequence < 1:
            raise ValueError("min_sequence deve ser um inteiro positivo")
        if any(category not in CATEGORIES for category in require_categories):
            raise ValueError("require_categories contém uma categoria desconhecida")
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                snapshot = self._snapshot_locked()
                if snapshot["meta"]["ready"] and not snapshot["meta"]["stale"] and snapshot["sequence"] >= min_sequence and all(snapshot["availability"][category]["available"] for category in require_categories):
                    return snapshot
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._running or self._stop_event.is_set():
                    return None
                self._condition.wait(remaining)

    def get_health(self) -> dict:
        snapshot = self.get_snapshot()
        meta = snapshot["meta"]
        errors = any(not item["success"] for item in snapshot["collectors"].values())
        status = "stopped" if not meta["running"] else "stale" if meta["stale"] else "degraded" if errors else "starting" if not meta["ready"] else "healthy"
        return {"status": status, "machine_id": self.machine_id, "hostname": self.hostname,
                "timestamp": snapshot["timestamp"], "sequence": snapshot["sequence"],
                "has_snapshot": snapshot["sequence"] > 0, "ready": meta["ready"],
                "fresh": snapshot["sequence"] > 0 and not meta["stale"], **meta,
                "collectors": snapshot["collectors"]}

    def prometheus_snapshot(self) -> tuple[bytes, bool, bool]:
        # O renderer é importado apenas quando a integração opcional é usada.
        from .common.metrics import render
        with self._condition:
            snapshot = self._snapshot_locked()
            samples = self._state[1] if self._state else ()
        ready = snapshot["meta"]["ready"]
        fresh = ready and not snapshot["meta"]["stale"] and snapshot["meta"]["running"]
        return render(samples, self.hostname), ready, fresh

    def stop(self, timeout: float = 5.0) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout deve ser finito e não negativo")
        with self._lifecycle:
            self._stop_event.set()
            with self._condition:
                self._condition.notify_all()
            worker = self._thread
            if worker is threading.current_thread():
                raise RuntimeError("stop não pode ser chamado pelo próprio coletor")
            if worker is not None:
                worker.join(timeout)
                if worker.is_alive():
                    raise RuntimeError("O coletor ainda está executando; chame stop novamente quando terminar")
            self._close_collectors()
            with self._condition:
                self._running = False
                self._condition.notify_all()

    def _close_collectors(self) -> None:
        for name, collector in self._collectors.items():
            close = getattr(collector, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    LOG.exception("Falha ao encerrar o coletor %s", name)
        self._collectors = {}

    def __enter__(self) -> "Monitor":
        return self.start()

    def __exit__(self, exception_type, exception, traceback) -> None:
        self.stop()
