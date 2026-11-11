"""Combina fontes explícitas e publica somente o snapshot JSON atual."""
import json
import math
from pathlib import Path
import socket
import threading
from datetime import datetime, timezone

from .dados import resultado
from .coletores.ebpf.cpu import CpuEbpf
from .coletores.ebpf.observador import Observador
from .coletores.sistema import armazenamento, gpu, nvidia, temperatura


def uso_memoria(kernel):
    source = "derived:ebpf/si_meminfo.total_bytes-ebpf/si_mem_available"
    if kernel["data"] is None:
        return resultado(source, error="Uso depende da amostra eBPF de memória; consulte memory")
    total = kernel["data"]["total_bytes"]
    free = kernel["data"]["available_bytes"]
    if total <= 0 or not 0 <= free <= total:
        return resultado(source, status="error", error="Amostras de memória inconsistentes")
    used = total - free
    return resultado(source, {"used_bytes": used, "usage_percent": 100 * used / total})


class Monitor:
    def __init__(self, interval=2, helper=None, observer_helper=None, sys=Path("/sys")):
        if not math.isfinite(interval) or not 0 < interval <= 86400:
            raise ValueError("Intervalo deve ser finito, > 0 e <= 86400 segundos")
        self.interval, self.helper, self.observer_helper = interval, helper, observer_helper
        self.sys = sys
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._payload = None
        self._thread = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("Monitor já iniciado")
        self._thread = threading.Thread(target=self._run, name="telemetria", daemon=True)
        self._thread.start()

    def _run(self):
        cpu = CpuEbpf(self.interval, self.helper)
        observer = Observador(self.interval, self.observer_helper)
        collectors = {
            "cpu": ("ebpf:sched_switch", cpu.collect),
            "storage": ("sysfs:block", lambda: armazenamento.coletar(self.sys)),
            "gpu": ("sysfs:drm", lambda: gpu.coletar(self.sys)),
            "gpu_nvidia": ("nvidia-smi", nvidia.coletar),
            "temperature": ("sysfs:hwmon", lambda: temperatura.coletar(self.sys)),
        }
        try:
            while not self._stop.is_set():
                snapshot = {"hostname": socket.gethostname(), "interval_seconds": self.interval,
                            **observer.collect()}
                for name, (source, collect) in collectors.items():
                    try:
                        snapshot[name] = collect()
                    except (OSError, ValueError, RuntimeError) as exc:
                        snapshot[name] = resultado(source, status="error", error=str(exc))
                snapshot["memory_usage"] = uso_memoria(snapshot["memory"])
                snapshot["timestamp"] = datetime.now(timezone.utc).isoformat()
                payload = json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode()
                with self._lock:
                    self._payload = payload
                if self._stop.wait(self.interval):
                    break
        finally:
            try:
                cpu.close()
            finally:
                observer.close()

    def snapshot(self):
        with self._lock:
            return self._payload

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=15)
            if self._thread.is_alive():
                raise RuntimeError("Coletor ainda bloqueado no kernel; encerramento incompleto")
