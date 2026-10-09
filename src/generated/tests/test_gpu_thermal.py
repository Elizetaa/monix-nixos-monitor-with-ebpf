"""Driver fixtures exercise absent telemetry and conservative sensor selection."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generated.collectors.gpu.collector import GpuCollector
from generated.collectors.thermal.collector import ThermalCollector
from generated.common.metrics import render
from generated.common.paths import SystemPaths


class HardwareFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = SystemPaths(proc=self.root / "proc", sys=self.root / "sys")

    def write(self, path: Path, value: str | int):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{value}\n")

    def gpu(self, card: str, pci: str, vendor: str, **attributes: str | int) -> Path:
        device = self.paths.sys / "devices/pci0000:00" / pci
        device.mkdir(parents=True, exist_ok=True)
        self.write(device / "vendor", vendor)
        self.write(device / "device", "0x1234")
        drm_card = self.paths.sys / "class/drm" / card
        drm_card.mkdir(parents=True, exist_ok=True)
        (drm_card / "device").symlink_to(device, target_is_directory=True)
        for name, value in attributes.items():
            self.write(device / name, value)
        return device

    def hwmon(self, number: int, name: str, **attributes: str | int) -> Path:
        directory = self.paths.sys / "class/hwmon" / f"hwmon{number}"
        self.write(directory / "name", name)
        for attribute, value in attributes.items():
            self.write(directory / attribute, value)
        return directory

    @staticmethod
    def named(samples, name):
        return [sample for sample in samples if sample.name == name]


class GpuTests(HardwareFixture):
    def setUp(self):
        super().setUp()
        mock = patch("generated.collectors.gpu.collector.subprocess.run", side_effect=FileNotFoundError())
        self.nvidia = mock.start()
        self.addCleanup(mock.stop)

    def test_amd_physical_card_identity_units_and_connector_deduplication(self):
        device = self.gpu("card7", "0000:03:00.0", "0x1002", gpu_busy_percent=42)
        self.gpu("card7-DP-1", "0000:03:00.0", "0x1002")
        self.gpu("card8", "0000:03:00.0", "0x1002")
        hwmon = device / "hwmon/hwmon3"
        for name, value in {"temp1_input": 48000, "temp1_label": "edge",
                            "fan1_input": 1234, "pwm1": 127}.items():
            self.write(hwmon / name, value)
        samples = GpuCollector(self.paths).collect()
        self.assertEqual(len(self.named(samples, "monix_gpu_info")), 1)
        usage = self.named(samples, "monix_gpu_usage_percent")[0]
        self.assertEqual(usage.value, 42)
        self.assertEqual(usage.labels["gpu"], "0000:03:00.0")
        self.assertEqual(usage.labels["vendor"], "amd")
        self.assertEqual(self.named(samples, "monix_gpu_temperature_celsius")[0].value, 48)
        self.assertEqual(self.named(samples, "monix_gpu_fan_speed_rpm")[0].value, 1234)
        self.assertFalse(self.named(samples, "monix_gpu_fan_speed_percent"))

    def test_intel_clock_is_not_reported_as_utilization(self):
        device = self.gpu("card0", "0000:00:02.0", "0x8086", gt_cur_freq_mhz=900)
        self.write(device / "hwmon/hwmon0/temp1_input", 45000)
        samples = GpuCollector(self.paths).collect()
        self.assertFalse(self.named(samples, "monix_gpu_usage_percent"))
        self.assertEqual(self.named(samples, "monix_gpu_temperature_celsius")[0].value, 45)

    def test_nvidia_merges_pci_identity_and_preserves_fan_percent(self):
        self.gpu("card1", "0000:01:00.0", "0x10de")
        self.nvidia.side_effect = None
        self.nvidia.return_value = subprocess.CompletedProcess(
            [], 0, '00000000:01:00.0, "NVIDIA Model, Example", 0, 55, 110\n', "")
        samples = GpuCollector(self.paths).collect()
        self.assertEqual(len(self.named(samples, "monix_gpu_info")), 1)
        info = self.named(samples, "monix_gpu_info")[0]
        self.assertEqual(info.labels["pci_address"], "0000:01:00.0")
        self.assertEqual(info.labels["model"], "NVIDIA Model, Example")
        self.assertEqual(self.named(samples, "monix_gpu_usage_percent")[0].value, 0)
        self.assertEqual(self.named(samples, "monix_gpu_fan_speed_percent")[0].value, 110)
        self.assertFalse(self.named(samples, "monix_gpu_fan_speed_rpm"))
        self.assertLessEqual(self.nvidia.call_args.kwargs["timeout"], 3)
        self.assertFalse(self.nvidia.call_args.kwargs["check"])

    def test_nvidia_unavailable_fields_are_omitted_for_headless_gpu(self):
        self.nvidia.side_effect = None
        self.nvidia.return_value = subprocess.CompletedProcess(
            [], 0, "00000000:04:00.0, NVIDIA GPU, N/A, 61, [Not Supported]\n", "")
        samples = GpuCollector(self.paths).collect()
        self.assertEqual(len(self.named(samples, "monix_gpu_info")), 1)
        self.assertFalse(self.named(samples, "monix_gpu_usage_percent"))
        self.assertFalse(self.named(samples, "monix_gpu_fan_speed_percent"))
        self.assertEqual(self.named(samples, "monix_gpu_temperature_celsius")[0].value, 61)

    def test_nvidia_sysfs_temperature_prevents_duplicate_cli_temperature(self):
        device = self.gpu("card0", "0000:01:00.0", "0x10de")
        self.write(device / "hwmon/hwmon0/temp1_input", 52000)
        self.nvidia.side_effect = None
        self.nvidia.return_value = subprocess.CompletedProcess(
            [], 0, "00000000:01:00.0, NVIDIA GPU, 10, 53, N/A\n", "")
        temperatures = self.named(GpuCollector(self.paths).collect(), "monix_gpu_temperature_celsius")
        self.assertEqual([sample.value for sample in temperatures], [52])

    def test_timeout_and_command_failure_leave_driver_telemetry_available(self):
        self.gpu("card0", "0000:01:00.0", "0x10de", gpu_busy_percent=7)
        for result in (subprocess.TimeoutExpired("nvidia-smi", 2),
                       subprocess.CompletedProcess([], 1, "", "driver unavailable")):
            with self.subTest(result=type(result).__name__):
                self.nvidia.side_effect = result if isinstance(result, Exception) else None
                self.nvidia.return_value = result
                samples = GpuCollector(self.paths).collect()
                self.assertEqual(self.named(samples, "monix_gpu_usage_percent")[0].value, 7)
                self.assertFalse(self.named(samples, "monix_gpu_temperature_celsius"))

    def test_disabled_faulty_and_nonfinite_gpu_inputs_are_omitted(self):
        device = self.gpu("card0", "0000:01:00.0", "0x1002", gpu_busy_percent="nan")
        hwmon = device / "hwmon/hwmon0"
        for name, value in {"temp1_input": 50000, "temp1_fault": 1,
                            "temp2_input": 50000, "temp2_enable": 0,
                            "temp3_input": 999000, "temp4_input": "invalid",
                            "temp4_type": 4, "fan1_input": "inf",
                            "fan2_input": -1}.items():
            self.write(hwmon / name, value)
        samples = GpuCollector(self.paths).collect()
        self.assertEqual([sample.name for sample in samples], ["monix_gpu_info"])

    def test_gpu_thermistor_type_does_not_change_temperature_input_units(self):
        device = self.gpu("card0", "0000:01:00.0", "0x1002")
        self.write(device / "hwmon/hwmon0/temp1_type", 4)
        self.write(device / "hwmon/hwmon0/temp1_input", 48000)
        temperatures = self.named(GpuCollector(self.paths).collect(), "monix_gpu_temperature_celsius")
        self.assertEqual([sample.value for sample in temperatures], [48])

    def test_multitile_hwmon_channels_have_distinct_prometheus_series(self):
        device = self.gpu("card0", "0000:01:00.0", "0x8086")
        for index, temperature in enumerate((45000, 47000)):
            hwmon = device / f"hwmon/hwmon{index}"
            self.write(hwmon / "name", "xe")
            self.write(hwmon / "temp1_input", temperature)
        samples = GpuCollector(self.paths).collect()
        temperatures = self.named(samples, "monix_gpu_temperature_celsius")
        self.assertEqual(len(temperatures), 2)
        self.assertNotEqual(temperatures[0].labels["sensor"], temperatures[1].labels["sensor"])
        self.assertIn(b"monix_gpu_temperature_celsius", render(samples, "fixture"))


class ThermalTests(HardwareFixture):
    def test_cpu_fault_disable_units_and_outliers(self):
        self.hwmon(0, "coretemp", temp1_input=49000, temp1_label="Package id 0",
                   temp2_input=47000, temp2_fault=1, temp3_input=40000,
                   temp3_enable=0, temp4_input=999000, temp5_input="nan", temp5_type=4)
        samples = ThermalCollector(self.paths).collect()
        cpu = self.named(samples, "monix_cpu_temperature_celsius")
        self.assertEqual([sample.value for sample in cpu], [49])
        self.assertEqual(cpu[0].labels, {"chip": "coretemp", "sensor": "temp1", "label": "Package id 0"})

    def test_nct_system_thermistor_temperature_is_collected_and_selected(self):
        self.hwmon(0, "nct6798", temp1_type=4, temp1_input=30000, temp1_label="SYSTIN")
        samples = ThermalCollector(self.paths).collect()
        board = self.named(samples, "monix_motherboard_temperature_celsius")
        self.assertEqual([sample.value for sample in board], [30])
        self.assertEqual(board[0].labels["role"], "system")
        estimate = self.named(samples, "monix_motherboard_ambient_estimate_celsius")
        self.assertEqual([sample.value for sample in estimate], [30])

    def test_air_sensors_take_priority_over_system_and_component_sensors(self):
        self.hwmon(0, "nct6798", temp1_input=32000, temp1_label="SYSTIN",
                   temp2_input=26000, temp2_label="Ambient", temp3_input=24000,
                   temp3_label="Inlet", temp4_input=60000, temp4_label="CPUTIN",
                   temp5_input=70000, temp5_label="VRM", temp6_input=56000,
                   temp6_label="PCH", temp7_input=127000, temp7_label="AUXTIN0",
                   temp8_input=90000, temp8_label="GPU System")
        samples = ThermalCollector(self.paths).collect()
        estimate = self.named(samples, "monix_motherboard_ambient_estimate_celsius")
        self.assertEqual([sample.value for sample in estimate], [25])
        self.assertIn("not measured room", estimate[0].help)
        self.assertEqual(len(self.named(samples, "monix_motherboard_temperature_celsius")), 8)

    def test_system_and_chassis_are_selected_when_air_sensors_are_absent(self):
        self.hwmon(0, "it8792", temp1_input=34000, temp1_label="System 1",
                   temp2_input=30000, temp2_label="Chassis", temp3_input=65000,
                   temp3_label="CPU")
        samples = ThermalCollector(self.paths).collect()
        self.assertEqual(self.named(samples, "monix_motherboard_ambient_estimate_celsius")[0].value, 32)

    def test_unlabeled_and_auxiliary_inputs_never_imply_ambient(self):
        self.hwmon(0, "nct6798", temp1_input=30000, temp2_input=25000,
                   temp2_label="AUXTIN0", temp3_input=26000, temp3_label="CPU Ambient")
        self.assertFalse(self.named(ThermalCollector(self.paths).collect(),
                                    "monix_motherboard_ambient_estimate_celsius"))

    def test_manual_sensor_selection_replaces_automatic_selection(self):
        self.hwmon(0, "nct6798", temp1_input=37000, temp2_input=26000,
                   temp2_label="Inlet")
        samples = ThermalCollector(self.paths, ("nct6798:temp1",)).collect()
        self.assertEqual(self.named(samples, "monix_motherboard_ambient_estimate_celsius")[0].value, 37)
        samples = ThermalCollector(self.paths, ("nct6798:temp9",)).collect()
        self.assertFalse(self.named(samples, "monix_motherboard_ambient_estimate_celsius"))

    def test_faulty_air_sensor_is_not_used_in_estimate(self):
        self.hwmon(0, "nct6798", temp1_input=30000, temp1_label="Inlet",
                   temp1_fault=1, temp2_input=32000, temp2_label="SYSTIN")
        samples = ThermalCollector(self.paths).collect()
        self.assertEqual(self.named(samples, "monix_motherboard_ambient_estimate_celsius")[0].value, 32)

    def test_cpu_zone_fallback_does_not_duplicate_hwmon_or_assume_acpi_location(self):
        zone = self.paths.sys / "class/thermal/thermal_zone0"
        self.write(zone / "type", "x86_pkg_temp")
        self.write(zone / "temp", 54000)
        acpi = self.paths.sys / "class/thermal/thermal_zone1"
        self.write(acpi / "type", "acpitz")
        self.write(acpi / "temp", 29000)
        collector = ThermalCollector(self.paths)
        self.assertEqual([sample.value for sample in self.named(collector.collect(),
                                                               "monix_cpu_temperature_celsius")], [54])
        self.hwmon(0, "coretemp", temp1_input=53000)
        self.assertEqual([sample.value for sample in self.named(collector.collect(),
                                                               "monix_cpu_temperature_celsius")], [53])

    def test_multiple_cpu_packages_have_unique_series_and_gpu_is_skipped(self):
        self.hwmon(0, "coretemp", temp1_input=53000)
        self.hwmon(1, "coretemp", temp1_input=54000)
        self.hwmon(2, "amdgpu", temp1_input=80000)
        samples = ThermalCollector(self.paths).collect()
        cpu = self.named(samples, "monix_cpu_temperature_celsius")
        self.assertEqual(len(cpu), 2)
        self.assertNotEqual(cpu[0].labels["chip"], cpu[1].labels["chip"])
        self.assertEqual(len(samples), 2)


if __name__ == "__main__":
    unittest.main()
