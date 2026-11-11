"""CPU exclusivamente eBPF: mantém o programa CO-RE e o loader existentes."""
import json
import math
from pathlib import Path

from .processo import Processo


class CpuEbpf(Processo):
    def __init__(self, interval, helper=None):
        helper = helper or Path(__file__).resolve().parents[2] / "build/cpu_usage"
        super().__init__(interval, helper, "ebpf:sched_switch")

    @staticmethod
    def decode(line):
        payload = json.loads(line)
        if not isinstance(payload, dict) or not isinstance(payload.get("cores"), dict) or not payload["cores"]:
            raise ValueError("Loader sem objeto cores válido")
        for key, value in payload["cores"].items():
            if not isinstance(key, str) or not key.isascii() or not key.isdecimal():
                raise ValueError("Identificador de CPU inválido")
        for value in [payload.get("usage_percent"), *payload["cores"].values()]:
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
                raise ValueError("Uso de CPU inválido no loader")
        return {"usage_percent": payload["usage_percent"], "cores": payload["cores"]}

