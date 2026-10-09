"""Read driver GPU telemetry; missing capabilities remain absent, never zero.

AMD exposes activity through ``gpu_busy_percent``. Intel activity is emitted
only when the driver exports the same explicit percentage attribute: clocks
cannot be converted into utilization. NVIDIA uses a bounded nvidia-smi query.
Its fan percentage is the intended speed, not a tachometer reading in RPM.
"""

from __future__ import annotations

import csv
import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ...common.metrics import Sample
from ...common.paths import SystemPaths, read_number, read_text


_VENDORS = {"0x1002": "amd", "0x10de": "nvidia", "0x8086": "intel"}
_PCI_ADDRESS = re.compile(r"^([0-9a-f]{4,8}):([0-9a-f]{2}):([0-9a-f]{2})\.([0-7])$", re.I)
_SENSOR = re.compile(r"^(temp[0-9]+|fan[0-9]+)_input$")
_NVIDIA_TIMEOUT_SECONDS = 2


@dataclass
class _Gpu:
    gpu: str
    pci_address: str
    vendor: str
    model: str
    device: Path | None = None

    @property
    def labels(self) -> dict[str, str]:
        return {"gpu": self.gpu, "pci_address": self.pci_address,
                "vendor": self.vendor, "model": self.model}


def _pci_address(text: str) -> str:
    match = _PCI_ADDRESS.fullmatch(text.strip())
    if not match:
        return ""
    domain, bus, device, function = match.groups()
    return f"{int(domain, 16):04x}:{bus.lower()}:{device.lower()}.{function}"


def _number(text: str) -> float | None:
    try:
        value = float(text.strip())
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def _valid_sensor(directory: Path, sensor: str) -> bool:
    return (read_number(directory / f"{sensor}_enable") != 0
            and read_number(directory / f"{sensor}_fault") in (None, 0))


class GpuCollector:
    def __init__(self, paths: SystemPaths):
        self.paths = paths

    def _devices(self) -> list[_Gpu]:
        devices: list[_Gpu] = []
        seen: set[Path] = set()
        for card in sorted((self.paths.sys / "class/drm").glob("card*")):
            if not re.fullmatch(r"card[0-9]+", card.name):
                continue
            device = card / "device"
            if not device.is_dir():
                continue
            real_device = device.resolve()
            if real_device in seen:
                continue
            seen.add(real_device)
            pci = _pci_address(real_device.name)
            if not pci:
                for line in read_text(device / "uevent").splitlines():
                    if line.startswith("PCI_SLOT_NAME="):
                        pci = _pci_address(line.partition("=")[2])
                        break
            vendor_id = read_text(device / "vendor").lower()
            driver = (device / "driver").resolve().name
            vendor = _VENDORS.get(vendor_id, {
                "amdgpu": "amd", "radeon": "amd", "i915": "intel",
                "xe": "intel", "nvidia": "nvidia", "nouveau": "nvidia",
            }.get(driver, "unknown"))
            # A virtual DRM card has no physical PCI/vendor identity. Platform
            # GPUs are retained when a real driver is attached.
            if not pci and not vendor_id and not (device / "driver").exists():
                continue
            model = (read_text(device / "product_name")
                     or read_text(device / "device") or driver or "unknown")
            devices.append(_Gpu(pci or card.name, pci, vendor, model, device))
        return devices

    def _nvidia(self) -> dict[str, tuple[str, float | None, float | None, float | None]]:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=pci.bus_id,name,utilization.gpu,temperature.gpu,fan.speed",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=_NVIDIA_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {}
        if result.returncode:
            return {}
        devices = {}
        for row in csv.reader(result.stdout.splitlines(), skipinitialspace=True):
            if len(row) != 5:
                continue
            pci = _pci_address(row[0])
            if not pci:
                continue
            devices[pci] = (row[1].strip(), _number(row[2]),
                            _number(row[3]), _number(row[4]))
        return devices

    def collect(self) -> list[Sample]:
        devices = self._devices()
        nvidia = self._nvidia()
        known_pci = {device.pci_address for device in devices}
        for pci, (model, *_values) in nvidia.items():
            if pci not in known_pci:
                devices.append(_Gpu(pci, pci, "nvidia", model or "unknown"))

        samples: list[Sample] = []
        for gpu in devices:
            nv = nvidia.get(gpu.pci_address) if gpu.vendor == "nvidia" else None
            if nv and nv[0]:
                gpu.model = nv[0]
            labels = gpu.labels
            samples.append(Sample("monix_gpu_info", "Physical GPU identity.", 1, dict(labels, source="nvidia-smi" if nv else "sysfs")))
            usage = nv[1] if nv else None
            usage_source = "nvidia-smi"
            if usage is None and gpu.device is not None:
                usage = read_number(gpu.device / "gpu_busy_percent")
                usage_source = "sysfs"
            if usage is not None and math.isfinite(usage) and 0 <= usage <= 100:
                samples.append(Sample("monix_gpu_usage_percent",
                                      "GPU utilization reported by the device driver, in percent.",
                                      usage, dict(labels, source=usage_source)))

            temperatures = 0
            if gpu.device is not None:
                directories = list((gpu.device / "hwmon").glob("hwmon*"))
                # Older drivers can expose the hwmon attributes directly.
                if any(gpu.device.glob("temp*_input")) or any(gpu.device.glob("fan*_input")):
                    directories.append(gpu.device)
                directories = list({directory.resolve(): directory for directory in directories}.values())
                source_names: dict[str, int] = {}
                seen_inputs: set[Path] = set()
                for directory in sorted(directories):
                    source = read_text(directory / "name") or directory.name
                    source_names[source] = source_names.get(source, 0) + 1
                    # Multi-tile GPUs can expose several hwmon devices whose
                    # local channel numbers coincide. Keep their series unique.
                    source = f"{source}:{source_names[source]}"
                    for path in sorted(directory.glob("*_input")):
                        match = _SENSOR.fullmatch(path.name)
                        if not match or path.resolve() in seen_inputs:
                            continue
                        seen_inputs.add(path.resolve())
                        sensor = match[1]
                        if not _valid_sensor(directory, sensor):
                            continue
                        value = read_number(path)
                        if value is None or not math.isfinite(value):
                            continue
                        sensor_id = sensor if len(directories) == 1 else f"{source}:{sensor}"
                        sensor_labels = dict(labels, sensor=sensor_id, source="hwmon",
                                             label=read_text(directory / f"{sensor}_label") or sensor)
                        if sensor.startswith("temp") and -40 <= value / 1000 <= 150:
                            temperatures += 1
                            samples.append(Sample("monix_gpu_temperature_celsius",
                                                  "GPU temperature reported by the device sensor, in Celsius.",
                                                  value / 1000, sensor_labels))
                        elif sensor.startswith("fan") and 0 <= value <= 100_000:
                            samples.append(Sample("monix_gpu_fan_speed_rpm",
                                                  "GPU fan tachometer speed, in revolutions per minute.",
                                                  value, sensor_labels))
            if nv:
                temperature, fan = nv[2], nv[3]
                if not temperatures and temperature is not None and -40 <= temperature <= 150:
                    samples.append(Sample("monix_gpu_temperature_celsius",
                                          "GPU temperature reported by the device sensor, in Celsius.",
                                          temperature, dict(labels, sensor="gpu", label="GPU", source="nvidia-smi")))
                if fan is not None and fan >= 0:
                    samples.append(Sample("monix_gpu_fan_speed_percent",
                                          "NVIDIA intended fan speed as percent of maximum noise tolerance speed; may exceed 100, not RPM.",
                                          fan, dict(labels, sensor="fan", label="GPU fan", source="nvidia-smi")))
        return samples
