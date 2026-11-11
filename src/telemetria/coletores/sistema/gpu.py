"""GPU DRM/sysfs. NVIDIA é consultada separadamente, sem mesclar fontes."""
import re

from ...dados import resultado
from .leitura import numero, texto
from .temperatura import sensor

VENDORS = {"0x1002": "amd", "0x10de": "nvidia", "0x8086": "intel"}


def coletar(sys):
    devices = []
    seen = set()
    for card in sorted((sys / "class/drm").glob("card*")):
        if not re.fullmatch(r"card\d+", card.name):
            continue
        device = card / "device"
        if not device.is_dir() or device.resolve() in seen:
            continue
        seen.add(device.resolve())
        usage_path = device / "gpu_busy_percent"
        usage = numero(usage_path)
        if usage is not None and not 0 <= usage <= 100:
            usage = None
        temperatures, fans = [], []
        directories = list((device / "hwmon").glob("hwmon*"))
        if any(device.glob("temp*_input")) or any(device.glob("fan*_input")):
            directories.append(device)
        for directory in directories:
            for path in sorted(directory.glob("temp*_input")):
                label = texto(directory / path.name.replace("_input", "_label")) or path.name
                reading = sensor(path, f"sysfs:{path}")
                temperatures.append({"label": label, **reading})
            for path in sorted(directory.glob("fan*_input")):
                channel = path.name.removesuffix("_input")
                if numero(directory / f"{channel}_enable") == 0 or numero(directory / f"{channel}_fault") not in (None, 0):
                    continue
                rpm = numero(path)
                if rpm is not None and not 0 <= rpm <= 100000:
                    rpm = None
                fans.append({"sensor": channel, **resultado(f"sysfs:{path}", rpm,
                             error=None if rpm is not None else "RPM indisponível")})
        devices.append({"id": card.name, "pci_address": device.resolve().name,
                        "vendor": VENDORS.get(texto(device / "vendor")),
                        "name": texto(device / "product_name") or texto(device / "device"),
                        "usage_percent": resultado(f"sysfs:{usage_path}", usage,
                                                  error=None if usage is not None else "Driver não expõe uso válido"),
                        "temperatures_celsius": temperatures, "fans_rpm": fans})
    return resultado("sysfs:drm", devices or None,
                     status="partial" if any(d["usage_percent"]["data"] is None for d in devices) else None,
                     error="Uso indisponível em algumas GPUs" if any(d["usage_percent"]["data"] is None for d in devices)
                     else None if devices else "Nenhuma GPU DRM exposta")
