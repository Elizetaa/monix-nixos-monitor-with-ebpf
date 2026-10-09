#!/usr/bin/env python3
"""Save one real in-memory snapshot. Disk export belongs only to this example."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from generated import Monitor
from generated.configuration import load_config, positive_interval


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, default=Path("metrics.json"))
    parser.add_argument("--interval", type=positive_interval)
    parser.add_argument("--timeout", type=positive_interval, help="Prazo da primeira amostra; padrão: 3 intervalos, mínimo 3 s.")
    parser.add_argument("--ebpf", choices=("auto", "required", "disabled"))
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        config = replace(config, **{
            name: value for name, value in (
                ("interval_seconds", args.interval), ("ebpf", args.ebpf)
            ) if value is not None
        })
        with Monitor(interval=config.interval_seconds, machine_id=config.machine_id,
                     ebpf=config.ebpf, cpu_helper=config.cpu_helper,
                     ambient_sensors=config.ambient_sensors, categories=config.collectors) as monitor:
            timeout = args.timeout or max(3.0, config.interval_seconds * 3)
            deadline = time.monotonic() + timeout
            cpu_required = config.ebpf == "required" and (config.collectors is None or "cpu" in config.collectors)
            snapshot = monitor.wait_for_snapshot(timeout=timeout, min_sequence=2,
                                                 require_categories=("cpu",) if cpu_required else ())
            while snapshot is not None and cpu_required and (
                snapshot["cpu"].get("usage_percent") is None or snapshot["cpu"].get("ebpf_attached") is not True
            ):
                snapshot = monitor.wait_for_snapshot(timeout=max(0.0, deadline - time.monotonic()),
                                                     min_sequence=snapshot["sequence"] + 1,
                                                     require_categories=("cpu",))
            if snapshot is None:
                raise RuntimeError("Nenhuma amostra válida disponível dentro do prazo.")
            args.output.write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
                                   encoding="utf-8")
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Não foi possível exportar métricas: {error}", file=sys.stderr)
        return 1
    print(f"Snapshot salvo em {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
