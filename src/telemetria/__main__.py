"""python -m telemetria: CLI mínima, sem configuração paralela em arquivo."""
import argparse
from pathlib import Path
import signal
import threading

from .api import servidor
from .coordenador import Monitor


def main():
    parser = argparse.ArgumentParser(description="Monitor Linux com coletores eBPF e API JSON")
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--helper", type=Path, help="Caminho do loader CPU cpu_usage")
    parser.add_argument("--observer-helper", type=Path, help="Caminho do loader observador")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9108)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Porta deve estar entre 1 e 65535")
    try:
        monitor = Monitor(args.interval, args.helper, args.observer_helper)
    except ValueError as exc:
        parser.error(str(exc))
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    with servidor(monitor, args.host, args.port) as server:
        server.timeout = 0.5
        monitor.start()
        print(f"GET http://{args.host}:{args.port}/api/metrics", flush=True)
        try:
            while not stop.is_set():
                server.handle_request()
        finally:
            monitor.stop()


if __name__ == "__main__":
    main()
