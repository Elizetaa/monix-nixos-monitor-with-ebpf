"""Fixtures só nos testes; coleta real e attach são verificações separadas."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from telemetria.api import servidor
from telemetria.coordenador import Monitor, uso_memoria
from telemetria.coletores.ebpf.cpu import CpuEbpf
from telemetria.coletores.ebpf.observador import Observador, SOURCES
from telemetria.dados import resultado
from telemetria.coletores.sistema import armazenamento, gpu, nvidia, temperatura


def aguardar(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.01)
    raise AssertionError("Tempo de espera excedido")


class ColetaTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc = self.root / "proc"
        self.sys = self.root / "sys"

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value))

    def test_memory_usage_is_derived_only_from_ebpf(self):
        kernel = resultado(SOURCES["memory"], {"total_bytes": 1000 * 1024, "available_bytes": 400 * 1024})
        self.assertEqual(uso_memoria(kernel)["data"], {"used_bytes": 600 * 1024, "usage_percent": 60})
        failure = resultado(SOURCES["memory"], status="error", error="EPERM")
        self.assertIsNone(uso_memoria(failure)["data"])
        kernel["data"]["available_bytes"] = 2000 * 1024
        self.assertEqual(uso_memoria(kernel)["status"], "error")

    def test_storage_inventory_has_no_filesystem_collector(self):
        self.write(self.sys / "class/block/sda/size", 4000)
        self.write(self.sys / "class/block/sda/dev", "8:0")
        with patch("os.statvfs", side_effect=AssertionError("Coleta de espaço deve usar BPF")):
            result = armazenamento.coletar(self.sys)
        self.assertEqual(result["data"]["blocks"][0]["capacity_bytes"]["data"], 4000 * 512)
        self.assertNotIn("filesystems", result["data"])
        (self.sys / "class/block/sda/size").unlink()
        self.assertEqual(armazenamento.coletar(self.sys)["status"], "partial")

    def test_system_temperature_does_not_read_thermal_zones(self):
        self.write(self.sys / "class/thermal/thermal_zone0/type", "x86_pkg_temp")
        self.write(self.sys / "class/thermal/thermal_zone0/temp", 54000)
        self.assertEqual(temperatura.coletar(self.sys)["status"], "unavailable")

    def test_gpu_clock_is_not_usage_and_sensors_use_real_units(self):
        device = self.sys / "class/drm/card0/device"
        self.write(device / "vendor", "0x8086")
        self.write(device / "gt_cur_freq_mhz", 900)
        self.write(device / "hwmon/hwmon0/temp1_input", 48000)
        self.write(device / "hwmon/hwmon0/fan1_input", 1200)
        data = gpu.coletar(self.sys)["data"][0]
        self.assertIsNone(data["usage_percent"]["data"])
        self.assertEqual(data["temperatures_celsius"][0]["data"], 48)
        self.assertEqual(data["fans_rpm"][0]["data"], 1200)

    def test_temperature_invalid_fault_and_disabled(self):
        directory = self.sys / "class/hwmon/hwmon0"
        self.write(directory / "name", "coretemp")
        for name, value in {"temp1_input": 49000, "temp2_input": 40000, "temp2_fault": 1,
                            "temp3_input": 40000, "temp3_enable": 0, "temp4_input": "nan"}.items():
            self.write(directory / name, value)
        result = temperatura.coletar(self.sys)
        self.assertEqual(result["status"], "partial")
        self.assertEqual([s["data"] for s in result["data"]], [49, None, None, None])

    def test_nvidia_failure_and_unsupported_fields_are_explicit(self):
        with patch.object(nvidia.subprocess, "run", side_effect=FileNotFoundError("sem nvidia-smi")):
            result = nvidia.coletar()
        self.assertEqual(result["status"], "error")
        with patch.object(nvidia.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, "00000000:01:00.0, NVIDIA, N/A, 50, 110\n", "")):
            data = nvidia.coletar()["data"][0]
        self.assertIsNone(data["usage_percent"]["data"])
        self.assertEqual(data["temperature_celsius"]["data"], 50)
        self.assertEqual(data["fan_percent"]["data"], 110)

    def test_ebpf_decoder_rejects_bad_samples(self):
        for payload in ({}, [], {"usage_percent": True, "cores": {"0": 2}},
                        {"usage_percent": 20, "cores": {}},
                        {"usage_percent": 20, "cores": {"0": float("nan")}}):
            with self.assertRaises(ValueError):
                CpuEbpf.decode(json.dumps(payload))

    def helper(self, text, name="cpu_usage"):
        path = self.root / name
        path.write_text(f"#!{os.sys.executable}\n" + text)
        path.chmod(0o755)
        return path

    def test_ebpf_stream_staleness_and_cleanup(self):
        helper = self.helper('import time\nprint(\'{"usage_percent":25,"cores":{"0":25}}\', flush=True)\ntime.sleep(30)\n')
        cpu = CpuEbpf(.01, helper)
        self.addCleanup(cpu.close)
        sample = aguardar(lambda: (r if (r := cpu.collect())["status"] == "available" else None))
        self.assertEqual(sample["data"]["usage_percent"], 25)
        with patch("telemetria.coletores.ebpf.processo.time.monotonic", return_value=time.monotonic() + 2):
            self.assertEqual(cpu.collect()["status"], "error")
        cpu.close()
        self.assertIsNotNone(cpu.process.poll())
        self.assertTrue(all(not t.is_alive() for t in cpu.threads))

    def test_loader_exit_clears_sample_and_exposes_stderr(self):
        helper = self.helper('import sys\nprint("attach negado", file=sys.stderr)\nsys.exit(1)\n')
        cpu = CpuEbpf(.01, helper)
        self.addCleanup(cpu.close)
        cpu.process.wait(timeout=2)
        for thread in cpu.threads:
            thread.join(timeout=1)
        result = cpu.collect()
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["data"])
        self.assertIn("attach negado", result["error"])

    @staticmethod
    def native_frame():
        frame = {name: resultado(source, status="error", error="hook indisponível") for name, source in SOURCES.items()}
        frame["memory"] = resultado(SOURCES["memory"], {"total_bytes": 1024000, "free_bytes": 102400,
                                                        "shared_bytes": 1024, "buffer_bytes": 0, "available_bytes": 409600})
        frame["memory_events"] = resultado(SOURCES["memory_events"], {"allocated_pages": 0, "freed_pages": 0,
                                                                      "page_size_bytes": 4096})
        frame["temperature_zones"] = resultado(SOURCES["temperature_zones"], status="pending", error="Sem evento")
        return frame

    def test_observer_rejects_incomplete_or_corrupt_frames(self):
        frame = self.native_frame()
        self.assertEqual(Observador.decode(json.dumps(frame)), frame)
        del frame["memory"]
        with self.assertRaises(ValueError):
            Observador.decode(json.dumps(frame))
        frame = self.native_frame()
        frame["memory_events"]["data"]["allocated_pages"] = float("nan")
        with self.assertRaises(ValueError):
            Observador.decode(json.dumps(frame))
        frame = self.native_frame()
        frame["block_io"]["data"] = {"invented": 0}
        with self.assertRaises(ValueError):
            Observador.decode(json.dumps(frame))

    def test_observer_keeps_independent_domains_and_discards_old_failed_data(self):
        good = json.dumps(self.native_frame())
        failed_frame = self.native_frame()
        failed_frame["memory"] = resultado(SOURCES["memory"], status="error", error="Leitura do mapa falhou")
        failed = json.dumps(failed_frame)
        helper = self.helper(f"import time\nprint({good!r}, flush=True)\ntime.sleep(.3)\nprint({failed!r}, flush=True)\ntime.sleep(30)\n")
        observer = Observador(.1, helper)
        self.addCleanup(observer.close)
        reading = aguardar(lambda: (r if (r := observer.collect())["memory"]["status"] == "available" else None))
        self.assertEqual(reading["block_io"]["status"], "error")
        self.assertEqual(reading["temperature_zones"]["status"], "pending")
        self.assertEqual(reading["memory_events"]["data"]["allocated_pages"], 0)
        reading = aguardar(lambda: (r if (r := observer.collect())["memory"]["error"] == "Leitura do mapa falhou" else None))
        self.assertIsNone(reading["memory"]["data"])
        self.assertEqual(reading["memory_events"]["status"], "available")
        observer.close()
        self.assertTrue(all(not t.is_alive() for t in observer.threads))

    def test_monitor_organizes_memory_from_ebpf_samples(self):
        cpu = self.helper("""import time
print('{"usage_percent":25,"cores":{"0":25}}', flush=True)
time.sleep(30)
""")
        frame = json.dumps(self.native_frame())
        observer = self.helper(f"import time\nprint({frame!r}, flush=True)\ntime.sleep(30)\n", "observador")
        monitor = Monitor(.1, helper=cpu, observer_helper=observer, sys=self.sys)
        monitor.start()
        self.addCleanup(monitor.stop)
        snapshot = aguardar(lambda: (s if (raw := monitor.snapshot()) and
                                    (s := json.loads(raw))["memory_usage"]["status"] == "available" else None))
        self.assertEqual(snapshot["memory_usage"]["data"]["usage_percent"], 60)
        self.assertEqual(snapshot["memory"]["source"], SOURCES["memory"])
        self.assertNotIn("memory_available", snapshot)
        monitor.stop()
        self.assertFalse(monitor._thread.is_alive())

    def test_endpoint_failure_isolation_no_fallback_and_shutdown(self):
        self.write(self.proc / "meminfo", "MemTotal: 1000 kB\nMemAvailable: 400 kB\n")
        monitor = Monitor(.02, helper=self.root / "missing", observer_helper=self.root / "missing",
                          sys=self.sys)
        server = servidor(monitor, port=0)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}"
        with self.assertRaises(HTTPError) as context:
            urlopen(url + "/api/metrics", timeout=2)
        self.assertEqual(context.exception.code, 503)
        context.exception.close()
        monitor.start()
        self.addCleanup(monitor.stop)
        aguardar(monitor.snapshot)
        with urlopen(url + "/api/metrics", timeout=2) as response:
            data = json.load(response)
            self.assertIn("application/json", response.headers["Content-Type"])
        self.assertEqual(data["cpu"]["status"], "error")
        self.assertEqual(data["cpu"]["source"], "ebpf:sched_switch")
        self.assertIsNone(data["cpu"]["data"])
        self.assertEqual(data["memory"]["status"], "error")
        self.assertIsNone(data["memory"]["data"])
        self.assertIsNone(data["memory_usage"]["data"])
        for name in SOURCES:
            self.assertEqual(data[name]["status"], "error")
            self.assertEqual(data[name]["source"], SOURCES[name])
        self.assertEqual(data["storage"]["status"], "error")
        with self.assertRaises(HTTPError) as context:
            urlopen(url + "/metrics", timeout=2)
        self.assertEqual(context.exception.code, 404)
        context.exception.close()
        monitor.stop()
        self.assertFalse(monitor._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
