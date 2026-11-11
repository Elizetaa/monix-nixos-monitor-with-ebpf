"""Um loader, objetos independentes e diagnósticos por domínio de métricas."""
import json
from pathlib import Path

from ...dados import resultado
from .processo import Processo

SOURCES = {
    "memory": "ebpf:fexit/si_meminfo,si_mem_available",
    "memory_events": "ebpf:raw_tp/mm_page_alloc,mm_page_free,mm_page_free_batched",
    "block_io": "ebpf:raw_tp/block_rq_issue,block_rq_complete",
    "temperature_zones": "ebpf:raw_tp/thermal_temperature",
    "gpu_events": "ebpf:raw_tp/drm_sched_job_queue,drm_sched_job_run,drm_sched_job_done",
    "filesystems": "ebpf:fexit/fd_statfs;inventory:procfs:/proc/self/mounts",
}


def invalid_constant(value):
    raise ValueError(f"Constante JSON inválida: {value}")


class Observador(Processo):
    def __init__(self, interval, helper=None):
        helper = helper or Path(__file__).resolve().parents[2] / "build/observador"
        super().__init__(interval, helper, "ebpf:observador")

    @staticmethod
    def decode(line):
        payload = json.loads(line, parse_constant=invalid_constant)
        if not isinstance(payload, dict) or payload.keys() != SOURCES.keys():
            raise ValueError("Categorias ausentes ou desconhecidas no loader")
        for name, source in SOURCES.items():
            reading = payload[name]
            if not isinstance(reading, dict) or set(reading) != {"source", "status", "data", "error"}:
                raise ValueError(f"Resultado inválido: {name}")
            if reading["source"] != source or reading["status"] not in {
                    "available", "partial", "unavailable", "pending", "error"}:
                raise ValueError(f"Fonte ou estado inválido: {name}")
            if reading["error"] is not None and not isinstance(reading["error"], str):
                raise ValueError(f"Diagnóstico inválido: {name}")
            if reading["status"] in {"error", "pending"} and reading["data"] is not None:
                raise ValueError(f"Dados inesperados em {name} com estado {reading['status']}")
        memory = payload["memory"]["data"]
        if memory is not None:
            fields = {"total_bytes", "free_bytes", "shared_bytes", "buffer_bytes", "available_bytes"}
            if not isinstance(memory, dict) or set(memory) != fields or any(
                    type(value) is not int or value < 0 for value in memory.values()):
                raise ValueError("Contadores de memória inválidos")
            if memory["total_bytes"] == 0 or max(memory["free_bytes"], memory["available_bytes"]) > memory["total_bytes"]:
                raise ValueError("Capacidade de RAM inválida")
        # Inclui números como 1e999, que o decoder JSON transforma em infinito.
        json.dumps(payload, allow_nan=False)
        return payload

    def collect(self):
        frame = super().collect()
        if frame["data"] is not None:
            return frame["data"]
        return {name: resultado(source, status=frame["status"], error=frame["error"])
                for name, source in SOURCES.items()}
