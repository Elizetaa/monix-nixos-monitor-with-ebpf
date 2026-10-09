"""Convert the existing collector samples into the public JSON contract.

This runs only during collection. HTTP readers consume the completed snapshot
and never access hardware. Missing readings remain ``None``; repeated sensors
remain individually identifiable. Scalar temperatures and fan values represent
the maximum reported value, with the complete readings retained in their lists.
"""

from __future__ import annotations

from collections import defaultdict
import math
from typing import Any, Iterable

from .common.metrics import Sample
from .common.paths import SystemPaths, read_text


def cpu_identification(paths: SystemPaths) -> dict[str, Any]:
    """Read Linux CPU identity without assuming an x86 cpuinfo layout."""
    fields: dict[str, str] = {}
    processors: set[str] = set()
    for line in read_text(paths.proc / "cpuinfo").splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key, value = key.strip(), value.strip()
        if not value:
            continue
        fields.setdefault(key, value)
        if key.casefold() == "processor" and value.isdecimal():
            processors.add(value)
    name = next((fields[key] for key in ("model name", "Hardware", "Processor", "cpu model")
                 if fields.get(key)), None)
    return {"name": name, "model": name,
            "vendor": fields.get("vendor_id") or fields.get("CPU implementer"),
            "logical_cores": len(processors) or None}


def _sensor(sample: Sample, source: str = "hwmon") -> dict[str, Any]:
    return {"chip": sample.labels.get("chip"),
            "sensor": sample.labels.get("sensor"),
            "label": sample.labels.get("label"),
            "temperature_celsius": float(sample.value),
            "source": sample.labels.get("source", source)}


def _maximum(values: Iterable[float]) -> float | None:
    return max(values, default=None)


def _devices(label: str) -> tuple[list[str], bool]:
    devices = sorted(set(filter(None, label.split(","))))
    return [device for device in devices if device != "unknown"], bool(devices) and "unknown" not in devices


def _gpus(families: dict[str, list[Sample]]) -> list[dict[str, Any]]:
    devices: dict[str, dict[str, Any]] = {}
    fans: dict[str, dict[tuple[str | None, str | None, str], dict[str, Any]]] = defaultdict(dict)
    names = ("monix_gpu_info", "monix_gpu_usage_percent", "monix_gpu_temperature_celsius",
             "monix_gpu_fan_speed_percent", "monix_gpu_fan_speed_rpm")
    for name in names:
        for sample in families.get(name, ()):
            labels = sample.labels
            identity = labels.get("gpu") or labels.get("pci_address")
            if not identity:
                continue
            gpu = devices.setdefault(identity, {
                "id": identity, "gpu": identity, "name": labels.get("model") or None,
                "model": labels.get("model") or None, "vendor": labels.get("vendor") or None,
                "pci_address": labels.get("pci_address") or None,
                "usage_percent": None, "temperature_celsius": None,
                "fan_percent": None, "fan_rpm": None, "temperatures": [], "fans": [],
                "sources": {"identity": None, "usage": None, "temperature": None,
                            "fan_percent": None, "fan_rpm": None},
            })
            source = labels.get("source", "nvidia-smi" if labels.get("vendor") == "nvidia"
                                and labels.get("sensor") in {"gpu", "fan"} else "sysfs")
            if name == "monix_gpu_info":
                gpu["sources"]["identity"] = labels.get("source")
            elif name == "monix_gpu_usage_percent":
                gpu["usage_percent"] = float(sample.value)
                # The vendor does not identify the interface: NVIDIA can also
                # fall back to gpu_busy_percent. Current collectors label the
                # source explicitly; older unlabelled samples remain unknown.
                gpu["sources"]["usage"] = labels.get("source")
            elif name == "monix_gpu_temperature_celsius":
                temperature = _sensor(sample, "hwmon" if source == "sysfs" else source)
                temperature.pop("chip")
                gpu["temperatures"].append(temperature)
            else:
                source = labels.get("source", "nvidia-smi" if name.endswith("percent") else "hwmon")
                key = (labels.get("sensor"), labels.get("label"), source)
                fan = fans[identity].setdefault(key, {
                    "sensor": key[0], "label": key[1], "source": source,
                    "percent": None, "rpm": None,
                })
                fan["percent" if name.endswith("percent") else "rpm"] = float(sample.value)
    for identity, gpu in sorted(devices.items()):
        gpu["temperatures"].sort(key=lambda item: (item["sensor"] or "", item["label"] or ""))
        gpu["fans"] = sorted(fans[identity].values(),
                             key=lambda item: (item["sensor"] or "", item["source"]))
        if gpu["temperatures"]:
            hottest = max(gpu["temperatures"], key=lambda item: item["temperature_celsius"])
            gpu["temperature_celsius"] = hottest["temperature_celsius"]
            gpu["sources"]["temperature"] = hottest["source"]
        for key, unit in (("fan_percent", "percent"), ("fan_rpm", "rpm")):
            readings = [fan for fan in gpu["fans"] if fan[unit] is not None]
            if readings:
                fastest = max(readings, key=lambda item: item[unit])
                gpu[key] = fastest[unit]
                gpu["sources"][key] = fastest["source"]
    return [devices[identity] for identity in sorted(devices)]


def _storage(families: dict[str, list[Sample]]) -> dict[str, list[dict[str, Any]]]:
    disks: dict[str, dict[str, Any]] = {}
    for name in ("monix_storage_disk_info", "monix_storage_disk_size_bytes"):
        for sample in families.get(name, ()):
            device = sample.labels.get("device")
            if not device:
                continue
            disk = disks.setdefault(device, {
                "device": device, "model": sample.labels.get("model") or None,
                "serial": sample.labels.get("serial") or None, "source": "sysfs",
                "total_bytes": None, "partitions": [], "filesystems": [],
            })
            if name.endswith("size_bytes"):
                disk["total_bytes"] = int(sample.value)

    partitions: dict[str, dict[str, Any]] = {}
    for name in ("monix_storage_partition_info", "monix_storage_partition_size_bytes"):
        for sample in families.get(name, ()):
            device = sample.labels.get("device")
            if not device:
                continue
            physical, known = _devices(sample.labels.get("physical_device", ""))
            partition = partitions.setdefault(device, {
                "device": device, "physical_device": physical[0] if known and len(physical) == 1 else None,
                "physical_devices": physical, "backing_known": known,
                "total_bytes": None, "source": "sysfs",
            })
            if name.endswith("size_bytes"):
                partition["total_bytes"] = int(sample.value)

    filesystems: dict[tuple[str, str, str], dict[str, Any]] = {}
    names = {f"monix_storage_filesystem_{field}_bytes": key for field, key in (
        ("size", "total_bytes"), ("used", "used_bytes"),
        ("free", "free_bytes"), ("available", "available_bytes"))}
    names["monix_storage_filesystem_exclusive"] = "exclusive"
    for name, field in names.items():
        for sample in families.get(name, ()):
            labels = sample.labels
            key = (labels.get("device", ""), labels.get("mountpoint", ""), labels.get("fstype", ""))
            physical, known = _devices(labels.get("physical_device", ""))
            filesystem = filesystems.setdefault(key, {
                "device": key[0] or None, "mountpoint": key[1] or None, "fstype": key[2] or None,
                "physical_devices": physical, "backing_known": known,
                "total_bytes": None, "used_bytes": None, "free_bytes": None, "available_bytes": None,
                "exclusive": known and len(physical) == 1 and key[2] != "btrfs", "source": "statvfs",
            })
            filesystem[field] = bool(sample.value) if field == "exclusive" else int(sample.value)

    unmapped_filesystems = []
    for _key, filesystem in sorted(filesystems.items()):
        mapped = [device for device in filesystem["physical_devices"] if device in disks]
        for device in mapped:
            disks[device]["filesystems"].append(filesystem)
        if not mapped or not filesystem["backing_known"]:
            unmapped_filesystems.append(filesystem)
    unmapped_partitions = []
    for _name, partition in sorted(partitions.items()):
        mapped = [device for device in partition["physical_devices"] if device in disks]
        for device in mapped:
            disks[device]["partitions"].append(partition)
        if not mapped or not partition["backing_known"]:
            unmapped_partitions.append(partition)
    return {"storage": [disks[device] for device in sorted(disks)],
            "unmapped_filesystems": unmapped_filesystems, "unmapped_partitions": unmapped_partitions}


def normalize(samples: Iterable[Sample], paths: SystemPaths) -> dict[str, Any]:
    """Build JSON-serializable hardware categories from one collection cycle."""
    families: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        if math.isfinite(sample.value):
            families[sample.name].append(sample)
    cpu = {**cpu_identification(paths), "source": None, "usage_percent": None,
           "temperature_celsius": None, "temperatures": [], "cores": [], "ebpf_attached": None}
    for sample in families.get("monix_cpu_usage_percent", ()):
        core = sample.labels.get("cpu")
        if core == "all":
            cpu["usage_percent"] = float(sample.value)
            cpu["source"] = sample.labels.get("source")
        elif core is not None:
            cpu["cores"].append({"cpu": core, "usage_percent": float(sample.value),
                                 "source": sample.labels.get("source")})
    cpu["cores"].sort(key=lambda core: (not core["cpu"].isdecimal(),
                                       int(core["cpu"]) if core["cpu"].isdecimal() else core["cpu"]))
    if cpu["logical_cores"] is None and cpu["cores"]:
        cpu["logical_cores"] = len(cpu["cores"])
    for sample in families.get("monix_cpu_temperature_celsius", ()):
        source = "sysfs" if sample.labels.get("sensor", "").startswith("thermal_zone") else "hwmon"
        cpu["temperatures"].append(_sensor(sample, source))
    cpu["temperatures"].sort(key=lambda item: (item["chip"] or "", item["sensor"] or ""))
    cpu["temperature_celsius"] = _maximum(item["temperature_celsius"] for item in cpu["temperatures"])
    attached = families.get("monix_cpu_ebpf_attached", ())
    if attached:
        cpu["ebpf_attached"] = bool(attached[0].value)

    memory: dict[str, Any] = {"source": "procfs", "usage_percent": None}
    for field in ("total", "used", "available", "free", "allocated"):
        readings = families.get(f"monix_memory_{field}_bytes", ())
        memory[f"{field}_bytes"] = int(readings[0].value) if readings else None
    if memory["total_bytes"] and memory["used_bytes"] is not None:
        memory["usage_percent"] = 100.0 * memory["used_bytes"] / memory["total_bytes"]

    result = {"cpu": cpu, "gpu": _gpus(families), "memory": memory, **_storage(families)}
    for category, metric in (("motherboard", "monix_motherboard_temperature_celsius"),
                             ("thermal", "monix_thermal_sensor_temperature_celsius")):
        sensors = [dict(_sensor(sample), role=sample.labels.get("role", "unknown"))
                   for sample in families.get(metric, ())]
        result[category] = sorted(sensors, key=lambda item: (item["chip"] or "", item["sensor"] or ""))
    ambient = families.get("monix_motherboard_ambient_estimate_celsius", ())
    result["ambient_estimate_celsius"] = float(ambient[0].value) if ambient else None
    return result
