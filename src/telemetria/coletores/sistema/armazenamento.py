"""Inventário e capacidade bruta de blocos; espaço de filesystems fica no eBPF."""
from ...dados import resultado
from .leitura import texto


def coletar(sys):
    blocks = []
    for path in sorted((sys / "class/block").iterdir()):
        if path.name.startswith(("loop", "zram", "ram")):
            continue
        try:
            capacity = int((path / "size").read_text()) * 512
            if capacity < 0:
                raise ValueError("Capacidade negativa")
            error = None
        except (OSError, UnicodeError, ValueError) as exc:
            capacity, error = None, str(exc)
        blocks.append({"device": path.name, "device_id": texto(path / "dev"),
                       "partition": (path / "partition").exists(),
                       "model": texto(path / "device/model"), "serial": texto(path / "device/serial"),
                       "capacity_bytes": resultado(f"sysfs:{path / 'size'}", capacity, error=error)})
    partial = any(b["capacity_bytes"]["data"] is None for b in blocks)
    return resultado("sysfs:block", {"blocks": blocks},
                     status="partial" if partial else "available" if blocks else "unavailable",
                     error="Algumas capacidades indisponíveis" if partial else None if blocks else "Nenhum bloco visível")
