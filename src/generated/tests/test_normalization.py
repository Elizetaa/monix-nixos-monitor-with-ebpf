"""Validate JSON accounting and sensor identities without physical hardware."""

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from generated.collectors.gpu.collector import GpuCollector
from generated.collectors.memory.collector import MemoryCollector
from generated.collectors.storage.collector import StorageCollector
from generated.collectors.thermal.collector import ThermalCollector
from generated.common.metrics import Sample
from generated.common.paths import SystemPaths
from generated.normalization import normalize


def metric(name, value, **labels):
    return Sample(f"monix_{name}", "fixture", value, labels)


class NormalizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = SystemPaths(proc=self.root / "proc", sys=self.root / "sys")
        self.paths.proc.mkdir()

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(text), encoding="utf-8")

    def test_unavailable_readings_are_null_and_serializable(self):
        data = normalize([], self.paths)
        self.assertIsNone(data["cpu"]["usage_percent"])
        self.assertIsNone(data["cpu"]["temperature_celsius"])
        self.assertIsNone(data["cpu"]["model"])
        self.assertIsNone(data["memory"]["total_bytes"])
        self.assertIsNone(data["memory"]["free_bytes"])
        self.assertEqual(data["gpu"], [])
        self.assertEqual(data["storage"], [])
        self.assertIsNone(data["ambient_estimate_celsius"])
        json.dumps(data, allow_nan=False)

    def test_cpu_identity_cores_and_maximum_preserve_individual_temperatures(self):
        self.write(self.paths.proc / "cpuinfo", "processor : 0\nmodel name : Test CPU\nvendor_id : TestVendor\n\nprocessor : 1\n")
        samples = [metric("cpu_usage_percent", 0, cpu="all", source="ebpf"),
                   metric("cpu_usage_percent", 30, cpu="10", source="ebpf"),
                   metric("cpu_usage_percent", 0, cpu="2", source="ebpf"),
                   metric("cpu_temperature_celsius", 42, chip="coretemp", sensor="temp1", label="Package id 0"),
                   metric("cpu_temperature_celsius", 51, chip="coretemp", sensor="temp2", label="Core 0"),
                   metric("cpu_ebpf_attached", 1)]
        data = normalize(samples, self.paths)["cpu"]
        self.assertEqual(data["name"], "Test CPU")
        self.assertEqual(data["vendor"], "TestVendor")
        self.assertEqual(data["logical_cores"], 2)
        self.assertEqual(data["usage_percent"], 0)
        self.assertEqual(data["source"], "ebpf")
        self.assertEqual(data["temperature_celsius"], 51)
        self.assertEqual([core["cpu"] for core in data["cores"]], ["2", "10"])
        self.assertEqual([item["label"] for item in data["temperatures"]], ["Package id 0", "Core 0"])
        self.assertTrue(data["ebpf_attached"])

    def test_arm_identity_and_thermal_zone_source(self):
        self.write(self.paths.proc / "cpuinfo", "processor : 0\nCPU implementer : 0x41\nHardware : ARM Board\n")
        data = normalize([metric("cpu_temperature_celsius", 47, chip="cpu_thermal", sensor="thermal_zone0")], self.paths)["cpu"]
        self.assertEqual(data["model"], "ARM Board")
        self.assertEqual(data["temperatures"][0]["source"], "sysfs")

    def test_ram_bytes_free_and_available_are_distinct(self):
        self.write(self.paths.proc / "meminfo", "MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\nCached: 300 kB\n")
        data = normalize(MemoryCollector(self.paths).collect(), self.paths)["memory"]
        self.assertEqual(data["total_bytes"], 1024000)
        self.assertEqual(data["free_bytes"], 102400)
        self.assertEqual(data["available_bytes"], 409600)
        self.assertEqual(data["used_bytes"], 614400)
        self.assertEqual(data["usage_percent"], 60)
        self.assertIs(type(data["total_bytes"]), int)
        self.assertEqual(data["source"], "procfs")

    def test_multiple_gpus_and_missing_fans_do_not_remove_other_data(self):
        first = {"gpu": "0000:01:00.0", "pci_address": "0000:01:00.0", "model": "GPU A", "vendor": "amd"}
        second = {"gpu": "0000:02:00.0", "pci_address": "0000:02:00.0", "model": "GPU B", "vendor": "nvidia"}
        samples = [metric("gpu_info", 1, **first, source="sysfs"),
                   metric("gpu_usage_percent", 0, **first, source="sysfs"),
                   metric("gpu_temperature_celsius", 45, **first, sensor="temp1", source="hwmon"),
                   metric("gpu_info", 1, **second, source="nvidia-smi"),
                   metric("gpu_temperature_celsius", 51, **second, sensor="gpu", source="nvidia-smi")]
        data = normalize(samples, self.paths)["gpu"]
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["usage_percent"], 0)
        self.assertEqual(data[0]["temperature_celsius"], 45)
        self.assertIsNone(data[0]["fan_percent"])
        self.assertIsNone(data[1]["usage_percent"])
        self.assertEqual(data[1]["sources"]["temperature"], "nvidia-smi")
        self.assertEqual(data[1]["fans"], [])

    def test_gpu_fan_units_remain_separate(self):
        samples = [metric("gpu_info", 1, gpu="gpu-1", model="GPU", vendor="nvidia"),
                   metric("gpu_fan_speed_rpm", 1200, gpu="gpu-1", sensor="fan1", label="front", source="hwmon"),
                   metric("gpu_fan_speed_rpm", 1400, gpu="gpu-1", sensor="fan2", label="rear", source="hwmon"),
                   metric("gpu_fan_speed_percent", 110, gpu="gpu-1", sensor="fan", source="nvidia-smi")]
        data = normalize(samples, self.paths)["gpu"][0]
        self.assertEqual(data["fan_rpm"], 1400)
        self.assertEqual(data["fan_percent"], 110)
        self.assertEqual(len(data["fans"]), 3)
        self.assertTrue(all(fan["rpm"] is None or fan["percent"] is None for fan in data["fans"]))

    def test_nvidia_usage_source_distinguishes_cli_and_sysfs_fallback(self):
        pci = "0000:01:00.0"
        device = self.paths.sys / "devices/pci0000:00" / pci
        self.write(device / "vendor", "0x10de")
        self.write(device / "device", "0x1234")
        self.write(device / "gpu_busy_percent", 7)
        card = self.paths.sys / "class/drm/card0"
        card.mkdir(parents=True)
        (card / "device").symlink_to(device)
        collector = GpuCollector(self.paths)
        for cli_usage, expected_source, expected_usage in ((42, "nvidia-smi", 42), (None, "sysfs", 7)):
            with self.subTest(source=expected_source):
                with patch.object(collector, "_nvidia", return_value={pci: ("NVIDIA GPU", cli_usage, 51, None)}):
                    samples = collector.collect()
                usage = next(sample for sample in samples if sample.name == "monix_gpu_usage_percent")
                self.assertEqual(usage.labels["source"], expected_source)
                data = normalize(samples, self.paths)["gpu"][0]
                self.assertEqual(data["usage_percent"], expected_usage)
                self.assertEqual(data["sources"]["usage"], expected_source)
                self.assertEqual(data["sources"]["temperature"], "nvidia-smi")
                self.assertIsNone(data["fan_percent"])

    def test_unlabelled_gpu_usage_does_not_guess_interface_from_vendor(self):
        data = normalize([metric("gpu_usage_percent", 7, gpu="gpu-1", vendor="nvidia")], self.paths)["gpu"][0]
        self.assertEqual(data["usage_percent"], 7)
        self.assertIsNone(data["sources"]["usage"])

    def test_shared_filesystem_capacity_is_not_aggregated_into_physical_disks(self):
        labels = {"device": "/dev/md0", "physical_device": "sda,sdb", "mountpoint": "/raid", "fstype": "ext4"}
        samples = [metric("storage_disk_size_bytes", 1000, device="sda", model="Disk A"),
                   metric("storage_disk_size_bytes", 2000, device="sdb", model="Disk B"),
                   metric("storage_filesystem_size_bytes", 900, **labels),
                   metric("storage_filesystem_available_bytes", 200, **labels),
                   metric("storage_filesystem_exclusive", 0, **labels)]
        data = normalize(samples, self.paths)
        self.assertEqual([disk["total_bytes"] for disk in data["storage"]], [1000, 2000])
        self.assertEqual(data["unmapped_filesystems"], [])
        for disk in data["storage"]:
            filesystem = disk["filesystems"][0]
            self.assertFalse(filesystem["exclusive"])
            self.assertEqual(filesystem["physical_devices"], ["sda", "sdb"])
            self.assertEqual(filesystem["total_bytes"], 900)
            self.assertNotIn("free_bytes", disk)

    def test_unknown_backing_and_incomplete_btrfs_are_explicit(self):
        labels = {"device": "/dev/sda1", "physical_device": "sda,unknown", "mountpoint": "/", "fstype": "btrfs"}
        samples = [metric("storage_disk_size_bytes", 1000, device="sda"),
                   metric("storage_filesystem_size_bytes", 900, **labels),
                   metric("storage_filesystem_exclusive", 0, **labels)]
        data = normalize(samples, self.paths)
        filesystem = data["storage"][0]["filesystems"][0]
        self.assertEqual(filesystem["physical_devices"], ["sda"])
        self.assertFalse(filesystem["backing_known"])
        self.assertFalse(filesystem["exclusive"])
        self.assertEqual(len(data["unmapped_filesystems"]), 1)
        self.assertIsNone(filesystem["free_bytes"])

    def test_physical_identity_survives_missing_capacity(self):
        data = normalize([metric("storage_disk_info", 1, device="sda", model="A", serial="123")], self.paths)
        self.assertEqual(data["storage"][0]["model"], "A")
        self.assertEqual(data["storage"][0]["serial"], "123")
        self.assertIsNone(data["storage"][0]["total_bytes"])

    def test_unmounted_partition_discovery_is_independent_of_statvfs(self):
        disk = self.paths.sys / "devices/block/sda"
        self.write(disk / "device/model", "Physical Disk")
        self.write(disk / "size", 4000)
        partition = disk / "sda1"
        self.write(partition / "partition", 1)
        self.write(partition / "size", 1000)
        block = self.paths.sys / "class/block"
        block.mkdir(parents=True)
        (block / "sda").symlink_to(disk)
        (block / "sda1").symlink_to(partition)
        with patch("generated.collectors.storage.collector.os.statvfs") as statvfs:
            data = normalize(StorageCollector(self.paths).collect(), self.paths)
        statvfs.assert_not_called()
        self.assertEqual(data["storage"][0]["total_bytes"], 4000 * 512)
        self.assertEqual(data["storage"][0]["partitions"][0]["total_bytes"], 1000 * 512)
        self.assertEqual(data["storage"][0]["partitions"][0]["physical_device"], "sda")
        self.assertEqual(data["storage"][0]["filesystems"], [])

    def test_mounted_filesystem_reports_used_free_and_available(self):
        disk = self.paths.sys / "class/block/sda"
        self.write(disk / "device/model", "Physical Disk")
        self.write(disk / "dev", "8:0")
        self.write(disk / "size", 1000)
        self.write(self.paths.proc / "self/mountinfo", "11 1 8:0 / /data rw - ext4 /dev/sda rw\n")
        stats = SimpleNamespace(f_blocks=100, f_bfree=40, f_bavail=30, f_frsize=1024, f_bsize=1024)
        with patch("generated.collectors.storage.collector.os.statvfs", return_value=stats):
            data = normalize(StorageCollector(self.paths).collect(), self.paths)
        filesystem = data["storage"][0]["filesystems"][0]
        self.assertEqual(filesystem["used_bytes"], 60 * 1024)
        self.assertEqual(filesystem["free_bytes"], 40 * 1024)
        self.assertEqual(filesystem["available_bytes"], 30 * 1024)
        self.assertTrue(filesystem["exclusive"])

    def test_chipset_and_explicit_vrm_sensors_are_board_components_not_ambient(self):
        for number, name, label, value in ((0, "pch_cannonlake", None, 55000), (1, "generic", "VRM", 65000)):
            directory = self.paths.sys / "class/hwmon" / f"hwmon{number}"
            self.write(directory / "name", name)
            self.write(directory / "temp1_input", value)
            if label:
                self.write(directory / "temp1_label", label)
        data = normalize(ThermalCollector(self.paths).collect(), self.paths)
        self.assertEqual({sensor["role"] for sensor in data["motherboard"]}, {"chipset", "vrm"})
        self.assertIsNone(data["ambient_estimate_celsius"])
        self.assertEqual(data["thermal"], [])

    def test_nonfinite_samples_do_not_break_json(self):
        data = normalize([metric("cpu_usage_percent", float("nan"), cpu="all", source="proc"),
                          metric("memory_total_bytes", float("inf"))], self.paths)
        self.assertIsNone(data["cpu"]["usage_percent"])
        self.assertIsNone(data["memory"]["total_bytes"])
        json.dumps(data, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
