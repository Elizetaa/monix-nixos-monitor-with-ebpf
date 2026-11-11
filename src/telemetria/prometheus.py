"""Converte o snapshot JSON no formato texto do Prometheus (exposition 0.0.4).

Só usa a biblioteca padrão. Leituras ausentes (null) não viram zero: a série
simplesmente não é emitida naquele ciclo, e o estado de cada categoria fica em
monix_category_status / monix_category_up para o Grafana mostrar o motivo.
"""
from datetime import datetime
import math

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# Categorias do contrato {source, status, data, error} publicadas pelo coordenador.
CATEGORIES = ("cpu", "memory", "memory_usage", "memory_events", "block_io", "filesystems",
              "temperature_zones", "gpu_events", "storage", "gpu", "gpu_nvidia", "temperature")
STATUSES = ("available", "partial", "unavailable", "pending", "error")


def _escape(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class _Registro:
    def __init__(self):
        self.families = {}

    def add(self, name, kind, help_text, value, **labels):
        value = _number(value)
        if value is None:
            return
        family = self.families.setdefault(name, (kind, help_text, []))
        family[2].append((labels, value))

    def render(self):
        lines = []
        for name, (kind, help_text, samples) in self.families.items():
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {kind}")
            for labels, value in samples:
                text = ",".join(f'{key}="{_escape(val)}"' for key, val in labels.items() if val is not None)
                lines.append(f"{name}{{{text}}} {value!r}" if text else f"{name} {value!r}")
        return ("\n".join(lines) + "\n").encode()


def _data(snapshot, name):
    reading = snapshot.get(name)
    return reading.get("data") if isinstance(reading, dict) else None


def renderizar(snapshot):
    r = _Registro()
    r.add("monix_info", "gauge", "Identificação do host monitorado.", 1,
          hostname=snapshot.get("hostname"))
    r.add("monix_interval_seconds", "gauge", "Intervalo entre coletas.", snapshot.get("interval_seconds"))
    try:
        ts = datetime.fromisoformat(snapshot["timestamp"]).timestamp()
    except (KeyError, TypeError, ValueError):
        ts = None
    r.add("monix_snapshot_timestamp_seconds", "gauge", "Instante (epoch) do último snapshot.", ts)

    for category in CATEGORIES:
        reading = snapshot.get(category)
        if not isinstance(reading, dict):
            continue
        r.add("monix_category_up", "gauge", "1 se a categoria publicou dados neste ciclo.",
              int(reading.get("data") is not None), category=category, source=reading.get("source"))
        for status in STATUSES:
            r.add("monix_category_status", "gauge", "Estado atual de cada categoria (1 no estado ativo).",
                  int(reading.get("status") == status), category=category, status=status)

    # CPU (eBPF sched_switch)
    cpu = _data(snapshot, "cpu")
    if isinstance(cpu, dict):
        r.add("monix_cpu_usage_percent", "gauge", "Uso total de CPU (eBPF sched_switch).", cpu.get("usage_percent"))
        for core, value in sorted((cpu.get("cores") or {}).items(), key=lambda kv: int(kv[0])):
            r.add("monix_cpu_core_usage_percent", "gauge", "Uso por núcleo (eBPF sched_switch).", value, core=core)

    # Memória (eBPF fexit si_meminfo / si_mem_available)
    memory = _data(snapshot, "memory")
    if isinstance(memory, dict):
        for field in ("total", "free", "shared", "buffer", "available"):
            r.add(f"monix_memory_{field}_bytes", "gauge", f"RAM {field} (eBPF fexit si_meminfo).",
                  memory.get(f"{field}_bytes"))
    usage = _data(snapshot, "memory_usage")
    if isinstance(usage, dict):
        r.add("monix_memory_used_bytes", "gauge", "RAM usada: total - disponível.", usage.get("used_bytes"))
        r.add("monix_memory_usage_percent", "gauge", "RAM usada em %.", usage.get("usage_percent"))
    pages = _data(snapshot, "memory_events")
    if isinstance(pages, dict):
        r.add("monix_memory_pages_allocated_total", "counter", "Páginas alocadas desde o attach (mm_page_alloc).",
              pages.get("allocated_pages"))
        r.add("monix_memory_pages_freed_total", "counter", "Páginas liberadas desde o attach (mm_page_free*).",
              pages.get("freed_pages"))
        r.add("monix_memory_page_size_bytes", "gauge", "Tamanho de página.", pages.get("page_size_bytes"))

    # Nomes de dispositivos (8:0 -> sda) vindos do inventário sysfs.
    names = {}
    storage = _data(snapshot, "storage")
    if isinstance(storage, dict):
        for block in storage.get("blocks") or []:
            if block.get("device_id"):
                names[block["device_id"]] = block.get("device")
            capacity = block.get("capacity_bytes") or {}
            r.add("monix_storage_capacity_bytes", "gauge", "Capacidade bruta do bloco/partição (sysfs).",
                  capacity.get("data"), device=block.get("device"), device_id=block.get("device_id"),
                  partition=str(bool(block.get("partition"))).lower(), model=block.get("model"))

    # I/O de blocos (eBPF block_rq_issue/complete)
    io = _data(snapshot, "block_io")
    if isinstance(io, dict):
        r.add("monix_block_io_lost_events_total", "counter", "Eventos de I/O perdidos (mapa cheio).",
              io.get("lost_events"))
        for dev in io.get("devices") or []:
            labels = {"device_id": dev.get("device_id"), "device": names.get(dev.get("device_id")),
                      "operation": dev.get("operation")}
            r.add("monix_block_io_issued_total", "counter", "Requests emitidos.", dev.get("issued"), **labels)
            r.add("monix_block_io_completions_total", "counter", "Eventos de conclusão.",
                  dev.get("completion_events"), **labels)
            r.add("monix_block_io_completed_bytes_total", "counter", "Bytes concluídos.",
                  dev.get("completed_bytes"), **labels)
            r.add("monix_block_io_errors_total", "counter", "Conclusões com erro.", dev.get("errors"), **labels)
            samples, mean_ms = _number(dev.get("latency_samples")), _number(dev.get("mean_latency_ms"))
            r.add("monix_block_io_latency_seconds_count", "counter", "Amostras de latência (emissão→conclusão).",
                  samples, **labels)
            if samples is not None and mean_ms is not None:
                # Soma reconstruída: permite rate(sum)/rate(count) = latência média na janela.
                r.add("monix_block_io_latency_seconds_sum", "counter", "Soma das latências (s).",
                      mean_ms * samples / 1000, **labels)
                r.add("monix_block_io_mean_latency_seconds", "gauge", "Latência média acumulada desde o attach.",
                      mean_ms / 1000, **labels)

    # Filesystems (eBPF fexit fd_statfs)
    for fs in _data(snapshot, "filesystems") or []:
        data = fs.get("data")
        if not isinstance(data, dict):
            continue
        labels = {"mountpoint": fs.get("mountpoint"), "device": fs.get("device"), "fstype": fs.get("fstype")}
        for field in ("total", "free", "available", "used"):
            r.add(f"monix_filesystem_{field}_bytes", "gauge", f"Espaço {field} (eBPF fd_statfs).",
                  data.get(f"{field}_bytes"), **labels)
        r.add("monix_filesystem_usage_percent", "gauge", "Uso do filesystem em %.", data.get("usage_percent"), **labels)

    # Zonas térmicas (eBPF thermal_temperature)
    for zone in _data(snapshot, "temperature_zones") or []:
        labels = {"zone": zone.get("id"), "type": zone.get("type")}
        r.add("monix_thermal_zone_temperature_celsius", "gauge", "Temperatura da zona (eBPF).",
              zone.get("temperature_celsius"), **labels)
        r.add("monix_thermal_zone_age_seconds", "gauge", "Idade do último evento térmico.",
              zone.get("age_seconds"), **labels)

    # Escalonador DRM (eBPF drm_sched_job_*)
    drm = _data(snapshot, "gpu_events")
    if isinstance(drm, dict):
        for field in ("queued", "run", "done"):
            r.add(f"monix_gpu_sched_jobs_{field}_total", "counter", f"Jobs DRM {field} desde o attach.",
                  drm.get(field))

    # GPU via sysfs/DRM
    for gpu in _data(snapshot, "gpu") or []:
        labels = {"card": gpu.get("id"), "pci_address": gpu.get("pci_address"),
                  "vendor": gpu.get("vendor"), "name": gpu.get("name")}
        r.add("monix_gpu_usage_percent", "gauge", "Uso da GPU (sysfs gpu_busy_percent).",
              (gpu.get("usage_percent") or {}).get("data"), **labels)
        for temp in gpu.get("temperatures_celsius") or []:
            r.add("monix_gpu_temperature_celsius", "gauge", "Temperatura da GPU (hwmon).", temp.get("data"),
                  card=gpu.get("id"), label=temp.get("label"))
        for fan in gpu.get("fans_rpm") or []:
            r.add("monix_gpu_fan_rpm", "gauge", "Ventoinha da GPU (hwmon).", fan.get("data"),
                  card=gpu.get("id"), sensor=fan.get("sensor"))

    # NVIDIA via nvidia-smi
    for gpu in _data(snapshot, "gpu_nvidia") or []:
        labels = {"pci_address": gpu.get("pci_address"), "name": gpu.get("name")}
        for field in ("usage_percent", "temperature_celsius", "fan_percent"):
            r.add(f"monix_nvidia_gpu_{field}", "gauge", f"NVIDIA {field} (nvidia-smi).",
                  (gpu.get(field) or {}).get("data"), **labels)

    # Demais sensores hwmon
    for sensor in _data(snapshot, "temperature") or []:
        r.add("monix_hwmon_temperature_celsius", "gauge", "Sensores hwmon.", sensor.get("data"),
              chip=sensor.get("chip"), sensor=sensor.get("sensor"), label=sensor.get("label"))

    return r.render()
