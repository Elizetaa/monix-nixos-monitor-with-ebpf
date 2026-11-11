"""Consulta externa isolada: falha de nvidia-smi é parte do resultado."""
import csv
import math
import subprocess

from ...dados import resultado


def coletar():
    try:
        command = subprocess.run(
            ["nvidia-smi", "--query-gpu=pci.bus_id,name,utilization.gpu,temperature.gpu,fan.speed",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=2)
        if command.returncode:
            raise RuntimeError(command.stderr.strip() or f"exit={command.returncode}")
        devices = []
        for row in csv.reader(command.stdout.splitlines(), skipinitialspace=True):
            if len(row) != 5:
                raise ValueError("Resposta CSV inválida")
            device = {"pci_address": row[0], "name": row[1]}
            for key, raw in zip(("usage_percent", "temperature_celsius", "fan_percent"), row[2:]):
                try:
                    value = float(raw)
                    valid = math.isfinite(value) and (0 <= value <= 100 if key == "usage_percent"
                            else -40 <= value <= 150 if key == "temperature_celsius" else value >= 0)
                except ValueError:
                    valid = False
                device[key] = resultado("nvidia-smi", value if valid else None,
                                         error=None if valid else f"Leitura indisponível: {raw}")
            devices.append(device)
        partial = any(device[key]["data"] is None for device in devices
                      for key in ("usage_percent", "temperature_celsius", "fan_percent"))
        error = "Algumas leituras NVIDIA indisponíveis" if partial else None
        return resultado("nvidia-smi", devices or None,
                         status="partial" if partial else None,
                         error=error if devices else "Nenhuma GPU retornada")
    except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
        return resultado("nvidia-smi", status="error", error=str(exc))
