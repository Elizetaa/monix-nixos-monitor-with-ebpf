"""Sensores reais hwmon; zonas térmicas são coletadas por eBPF; preserva rótulos sem inferir ambiente."""
import re

from ...dados import resultado
from .leitura import numero, texto


def sensor(path, source):
    channel = path.name.removesuffix("_input")
    if numero(path.parent / f"{channel}_enable") == 0:
        return resultado(source, error="Sensor desabilitado")
    if numero(path.parent / f"{channel}_fault") not in (None, 0):
        return resultado(source, error="Sensor sinaliza falha")
    raw = numero(path)
    value = raw / 1000 if raw is not None else None
    if value is None:
        return resultado(source, error=f"Não foi possível ler {path}")
    if not -40 <= value <= 150:
        return resultado(source, error=f"Temperatura fora da faixa: {path}")
    return resultado(source, value)


def coletar(sys):
    sensors = []
    seen = set()
    for directory in sorted((sys / "class/hwmon").glob("hwmon*")):
        for path in sorted(directory.glob("temp*_input")):
            if not re.fullmatch(r"temp\d+_input", path.name) or path.resolve() in seen:
                continue
            seen.add(path.resolve())
            channel = path.name.removesuffix("_input")
            label = texto(directory / f"{channel}_label") or channel
            reading = sensor(path, f"sysfs:{path}")
            sensors.append({"chip": texto(directory / "name"), "sensor": channel,
                            "label": label, **reading})
    return resultado("sysfs:hwmon", sensors or None,
                     status="partial" if any(s["data"] is None for s in sensors) else None,
                     error="Alguns sensores não puderam ser lidos" if any(s["data"] is None for s in sensors)
                     else None if sensors else "Nenhum sensor habilitado exposto pelo kernel")
