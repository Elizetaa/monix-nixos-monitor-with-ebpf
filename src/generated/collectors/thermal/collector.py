"""Read valid hwmon temperatures and conservative case-air estimates.

Hwmon channel numbers do not encode physical locations. Only explicit labels
such as Inlet, Ambient, SYSTIN, System, Motherboard and Chassis are selected for
an automatic estimate; CPU/GPU/VRM/PCH and AUXTIN inputs are excluded. Manual
``chip:tempN`` selectors can identify sensors whose physical placement the user
has verified. This measures inlet/internal case air, not room temperature.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

from ...common.metrics import Sample
from ...common.paths import SystemPaths, read_number, read_text


_CPU_CHIPS = {"coretemp", "k10temp", "k8temp", "zenpower", "zenpower3",
              "via_cputemp", "cpu_thermal", "x86_pkg_temp", "loongson_cpu",
              "peci_cputemp", "intel_peci_cputemp"}
_GPU_CHIPS = {"amdgpu", "radeon", "nouveau", "nvidia", "i915", "xe"}
_BOARD_PREFIXES = ("nct", "it87", "it86", "w836", "w837", "f718", "f753",
                   "asus", "gigabyte", "dell_smm", "dell-smm", "thinkpad", "sch56", "pch_")
_AMBIENT_HELP = ("Estimated inlet or internal chassis air temperature from explicitly identified "
                 "motherboard sensors, in Celsius; this is not measured room temperature.")


def _temperature(path: Path) -> float | None:
    value = read_number(path)
    if value is None or not math.isfinite(value):
        return None
    value /= 1000
    return value if -40 <= value <= 150 else None


def _role(label: str) -> str:
    label = re.sub(r"[^a-z0-9]+", " ", label.casefold()).strip()
    if not label:
        return "unknown"
    # Component labels take precedence over air/board words in compound labels.
    for role, pattern in (
        ("cpu", r"cpu|cputin|peci|package|core|tctl|tdie|tccd"),
        ("gpu", r"gpu|graphics|junction"),
        ("vrm", r"vrm|vcore|mosfet|\bmos\b"),
        ("chipset", r"pch|chipset|\bsoc\b"),
        ("auxiliary", r"auxtin|\baux[0-9]*\b|smbus"),
        ("inlet", r"inlet|intake"),
        ("ambient", r"ambient"),
        ("system", r"systin|\bsystem\b"),
        ("motherboard", r"motherboard|mainboard"),
        ("chassis", r"chassis|\bcase\b"),
    ):
        if re.search(pattern, label):
            return role
    return "unknown"


def _chip_suffix(directory: Path) -> str:
    device = directory / "device"
    if device.exists():
        return device.resolve().name
    real = directory.resolve()
    if real.parent.name == "hwmon":
        return real.parent.parent.name
    return directory.name


class ThermalCollector:
    def __init__(self, paths: SystemPaths, ambient_sensors: tuple[str, ...] = ()):
        self.paths = paths
        self.ambient_sensors = frozenset(ambient_sensors)

    def collect(self) -> list[Sample]:
        directories: list[tuple[Path, str]] = []
        seen: set[Path] = set()
        for directory in sorted((self.paths.sys / "class/hwmon").glob("hwmon*")):
            real = directory.resolve()
            if real in seen:
                continue
            seen.add(real)
            directories.append((directory, read_text(directory / "name") or directory.name))
        counts = Counter(name for _, name in directories)
        samples: list[Sample] = []
        ambient: list[tuple[int, float]] = []
        cpu_present = False
        identities: set[str] = set()
        for directory, name in directories:
            if name.casefold() in _GPU_CHIPS:
                continue
            chip = name if counts[name] == 1 else f"{name}@{_chip_suffix(directory)}"
            if chip in identities:
                chip = f"{chip}@{directory.name}"
            identities.add(chip)
            cpu_chip = name.casefold() in _CPU_CHIPS
            board_chip = name.casefold().startswith(_BOARD_PREFIXES)
            for path in sorted(directory.glob("temp*_input")):
                if not re.fullmatch(r"temp[0-9]+_input", path.name):
                    continue
                sensor = path.name.removesuffix("_input")
                if (read_number(directory / f"{sensor}_enable") == 0
                        or read_number(directory / f"{sensor}_fault") not in (None, 0)):
                    continue
                temperature = _temperature(path)
                if temperature is None:
                    continue
                raw_label = read_text(directory / f"{sensor}_label")
                labels = {"chip": chip, "sensor": sensor, "label": raw_label or sensor}
                if cpu_chip:
                    cpu_present = True
                    samples.append(Sample("monix_cpu_temperature_celsius",
                                          "CPU hardware temperature in Celsius; Tctl can be a cooling-control value rather than die temperature.",
                                          temperature, labels))
                    continue
                role = _role(raw_label)
                if role == "unknown" and name.casefold().startswith("pch_"):
                    role = "chipset"
                selected = bool({f"{name}:{sensor}", f"{chip}:{sensor}"} & self.ambient_sensors)
                motherboard = board_chip or selected or role in {
                    "inlet", "ambient", "system", "motherboard", "chassis", "chipset", "vrm"}
                if not motherboard:
                    samples.append(Sample("monix_thermal_sensor_temperature_celsius",
                                          "Other hardware sensor temperature, in Celsius.", temperature, dict(labels, role=role)))
                    continue
                samples.append(Sample("monix_motherboard_temperature_celsius",
                                      "Motherboard hardware sensor temperature, in Celsius.",
                                      temperature, dict(labels, role=role)))
                # An explicit selection replaces automatic selection, and never
                # falls back silently when a selected sensor disappears.
                if self.ambient_sensors:
                    if selected and -20 <= temperature <= 100:
                        ambient.append((0, temperature))
                elif role in {"inlet", "ambient", "system", "motherboard", "chassis"}:
                    if -20 <= temperature <= 100:
                        ambient.append((0 if role in {"inlet", "ambient"} else 1, temperature))
        if not cpu_present:
            samples.extend(self._cpu_thermal_zones())
        if ambient:
            priority = min(item[0] for item in ambient)
            values = [value for rank, value in ambient if rank == priority]
            samples.append(Sample("monix_motherboard_ambient_estimate_celsius", _AMBIENT_HELP,
                                  sum(values) / len(values)))
        return samples

    def _cpu_thermal_zones(self) -> list[Sample]:
        samples: list[Sample] = []
        seen: set[Path] = set()
        for zone in sorted((self.paths.sys / "class/thermal").glob("thermal_zone*")):
            if not re.fullmatch(r"thermal_zone[0-9]+", zone.name) or zone.resolve() in seen:
                continue
            seen.add(zone.resolve())
            kind = read_text(zone / "type")
            normalized = kind.casefold()
            if "cpu" not in normalized and normalized not in {"x86_pkg_temp", "processor"}:
                continue
            if read_text(zone / "mode").casefold() == "disabled":
                continue
            temperature = _temperature(zone / "temp")
            if temperature is not None:
                samples.append(Sample("monix_cpu_temperature_celsius",
                                      "CPU hardware temperature in Celsius; Tctl can be a cooling-control value rather than die temperature.",
                                      temperature, {"chip": kind, "sensor": zone.name, "label": kind}))
        return samples
