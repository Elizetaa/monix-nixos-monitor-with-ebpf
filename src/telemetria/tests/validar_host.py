"""Validação real (sem fixtures), inclusive HTTP, referências do Linux e SIGTERM.

Execute com --require-ebpf para exigir attach de CPU, memória, páginas, I/O e
filesystems. DRM/zonas podem estar ausentes ou sem eventos, conforme hardware.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-ebpf", action="store_true")
    args = parser.parse_args()
    source_root = Path(__file__).resolve().parents[2]
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    environment = dict(os.environ, PYTHONPATH=str(source_root), PYTHONDONTWRITEBYTECODE="1")
    required = ("cpu", "memory", "memory_events", "block_io", "filesystems")
    with tempfile.TemporaryFile(mode="w+") as log:
        process = subprocess.Popen([sys.executable, "-m", "telemetria", "--interval", ".2", "--port", str(port)],
                                   env=environment, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    log.seek(0)
                    raise AssertionError(log.read())
                try:
                    with urlopen(f"http://127.0.0.1:{port}/api/metrics", timeout=3) as response:
                        assert response.status == 200
                        assert response.headers["Content-Type"].startswith("application/json")
                        snapshot = json.load(response)
                    if all(snapshot[name]["status"] != "pending" for name in required):
                        break
                except URLError:
                    pass
                time.sleep(.1)
            else:
                raise AssertionError("Coleta inicial não concluída no prazo")
            for name, reading in snapshot.items():
                if not isinstance(reading, dict):
                    continue
                assert set(reading) == {"source", "status", "data", "error"}, name
                if reading["status"] == "error":
                    assert reading["data"] is None and reading["error"], name
            memory = snapshot["memory"]["data"]
            if memory is not None:
                total_line = next(line for line in Path("/proc/meminfo").read_text().splitlines()
                                  if line.startswith("MemTotal:"))
                assert memory["total_bytes"] == int(total_line.split()[1]) * 1024
                assert 0 <= memory["free_bytes"] <= memory["total_bytes"]
            for fs in snapshot["filesystems"]["data"] or []:
                if fs["data"] is None:
                    continue
                # statvfs é apenas a referência do teste, nunca uma fonte em produção.
                stats = os.statvfs(fs["mountpoint"])
                assert fs["data"]["total_bytes"] == stats.f_blocks * stats.f_frsize
                assert 0 <= fs["data"]["available_bytes"] <= fs["data"]["free_bytes"] <= fs["data"]["total_bytes"]
            summary = {name: reading["status"] for name, reading in snapshot.items() if isinstance(reading, dict)}
            print(json.dumps(summary, ensure_ascii=False))
            for name in required:
                if snapshot[name]["status"] == "error":
                    print(f"{name}: {snapshot[name]['error']}")
            if args.require_ebpf:
                assert all(snapshot[name]["status"] == "available" for name in required), "Attach eBPF incompleto"
            print("HTTP, fontes, ausência de fallback e referências disponíveis: OK")
        finally:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=18)
            if process.returncode != 0:
                log.seek(0)
                raise AssertionError(log.read())
            print("Encerramento por SIGTERM: OK")


if __name__ == "__main__":
    main()
