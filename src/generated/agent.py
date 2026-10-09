"""Run the reusable monitor with a local JSON API and optional Prometheus."""

import argparse
from dataclasses import replace
import json
import logging
from pathlib import Path
import signal
import threading
import time

from .api import create_server
from .common.paths import SystemPaths
from .configuration import AgentConfig, COLLECTORS, load_config, positive_interval, valid_port

LOG = logging.getLogger("monix")


def arguments(argv: list[str] | None = None, *, legacy: bool = False) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Arquivo TOML; opções CLI têm precedência.")
    parser.add_argument("--interval", type=positive_interval, help="Intervalo entre inícios de coletas, em segundos.")
    parser.add_argument("--machine-id", help="Identificador estável explícito da máquina.")
    parser.add_argument("--listen", "--host", dest="host", help="Endereço HTTP (padrão: 127.0.0.1).")
    parser.add_argument("--port", type=valid_port)
    parser.add_argument("--ebpf", choices=("auto", "required", "disabled"))
    parser.add_argument("--cpu-helper", type=Path)
    parser.add_argument("--ambient-sensor", action="append", metavar="CHIP:TEMPN")
    parser.add_argument("--collector", choices=("all", *COLLECTORS))
    parser.add_argument("--hostname", help="Hostname nas métricas; machine_id identifica a máquina separadamente.")
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"), help=argparse.SUPPRESS)
    parser.add_argument("--sys-root", type=Path, default=Path("/sys"), help=argparse.SUPPRESS)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--prometheus", dest="prometheus", action="store_true", default=None,
                       help="Habilita GET /metrics usando o mesmo snapshot.")
    group.add_argument("--no-prometheus", dest="prometheus", action="store_false")
    parser.add_argument("--once", action="store_true", help="Aguarda uma amostra válida, imprime e encerra.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if legacy and args.config is None:
            config = replace(config, prometheus_enabled=True)
        overrides = {
            "interval_seconds": args.interval,
            "machine_id": args.machine_id,
            "host": args.host,
            "port": args.port,
            "ebpf": args.ebpf,
            "cpu_helper": args.cpu_helper,
            "ambient_sensors": tuple(args.ambient_sensor) if args.ambient_sensor is not None else None,
            "prometheus_enabled": args.prometheus,
        }
        if args.collector is not None:
            overrides["collectors"] = None if args.collector == "all" else (args.collector,)
        changes = {name: value for name, value in overrides.items() if value is not None}
        if args.collector == "all":
            changes["collectors"] = None
        args.configuration = replace(config, **changes)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    return args


def _wait_once(monitor, config: AgentConfig) -> dict | None:
    # CPU counters require a baseline; required eBPF must actually publish usage.
    timeout = max(1.0, config.interval_seconds * 3) + 0.25
    deadline = time.monotonic() + timeout
    cpu_enabled = config.collectors is None or "cpu" in config.collectors
    snapshot = monitor.wait_for_snapshot(timeout=timeout, min_sequence=2 if cpu_enabled else 1)
    while snapshot is not None and cpu_enabled:
        cpu = snapshot["cpu"]
        if cpu.get("usage_percent") is not None and (
            config.ebpf != "required" or cpu.get("ebpf_attached") is True
        ):
            return snapshot
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        snapshot = monitor.wait_for_snapshot(timeout=remaining, min_sequence=snapshot["sequence"] + 1)
    return snapshot


def main(argv: list[str] | None = None, *, legacy: bool = False) -> int:
    from . import Monitor

    args = arguments(argv, legacy=legacy)
    config = args.configuration
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    monitor = None
    stopped = threading.Event()
    previous_handlers = {}
    result = 0
    try:
        monitor = Monitor(
            interval=config.interval_seconds, machine_id=config.machine_id, ebpf=config.ebpf,
            cpu_helper=config.cpu_helper, ambient_sensors=config.ambient_sensors,
            paths=SystemPaths(args.proc_root, args.sys_root), hostname=args.hostname,
            categories=config.collectors,
        )
        if args.once:
            monitor.start()
            snapshot = _wait_once(monitor, config)
            if legacy:
                print(monitor.prometheus_snapshot()[0].decode("utf-8"), end="")
            elif snapshot is not None:
                print(json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
            if snapshot is None:
                LOG.error("Nenhuma amostra válida disponível dentro do prazo.")
                result = 1
            elif any(item["status"] == "error" for item in snapshot["availability"].values()):
                result = 1
        else:
            with create_server(monitor, config.host, config.port, config.prometheus_enabled) as server:
                server.timeout = 0.5
                monitor.start()
                if threading.current_thread() is threading.main_thread():
                    for sig in (signal.SIGINT, signal.SIGTERM):
                        previous_handlers[sig] = signal.signal(sig, lambda signum, frame: stopped.set())
                LOG.info("API em http://%s:%d/api/metrics", config.host, server.server_port)
                if config.prometheus_enabled:
                    LOG.info("Prometheus em http://%s:%d/metrics", config.host, server.server_port)
                while not stopped.is_set():
                    server.handle_request()
    except (OSError, RuntimeError, ValueError) as error:
        LOG.error("Não foi possível executar o agente: %s", error, exc_info=args.verbose)
        result = 1
    finally:
        for sig, previous in previous_handlers.items():
            signal.signal(sig, previous)
        if monitor is not None:
            try:
                monitor.stop(timeout=5.0)
            except (OSError, RuntimeError) as error:
                LOG.error("Falha ao encerrar o monitor: %s", error)
                result = 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
