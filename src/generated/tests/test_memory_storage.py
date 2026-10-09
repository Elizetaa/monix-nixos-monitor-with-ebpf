"""Hermetic RAM and storage checks; no hardware access or privileges required."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from generated.collectors.memory.collector import MemoryCollector
from generated.collectors.storage.collector import StorageCollector
from generated.common.paths import SystemPaths


class CollectorFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = SystemPaths(proc=self.root / "proc", sys=self.root / "sys")
        (self.paths.proc / "self").mkdir(parents=True)
        (self.paths.sys / "class/block").mkdir(parents=True)
        (self.paths.sys / "dev/block").mkdir(parents=True)
        self.block_paths = {}

    def block(self, name, device_id, *, parent=None, physical=False, sectors=1000):
        if parent:
            target = self.block_paths[parent] / name
        else:
            target = self.paths.sys / "devices/block" / name
        target.mkdir(parents=True)
        (target / "dev").write_text(device_id)
        (target / "size").write_text(str(sectors))
        if parent:
            (target / "partition").write_text("1")
        if physical:
            (target / "device").mkdir()
            (target / "device/model").write_text(f"Model {name}\n")
            (target / "device/serial").write_text(f"Serial {name}\n")
        (self.paths.sys / "class/block" / name).symlink_to(target, target_is_directory=True)
        (self.paths.sys / "dev/block" / device_id).symlink_to(target, target_is_directory=True)
        self.block_paths[name] = target
        return target

    def slaves(self, name, *children):
        target = self.block_paths[name] / "slaves"
        target.mkdir()
        for child in children:
            destination = self.block_paths.get(child, self.paths.sys / "missing" / child)
            (target / child).symlink_to(destination, target_is_directory=True)

    def mounts(self, *lines):
        (self.paths.proc / "self/mountinfo").write_text("\n".join(lines) + "\n")

    @staticmethod
    def capacity(blocks=100, free=40, available=30, fragment=4096):
        return SimpleNamespace(f_blocks=blocks, f_bfree=free, f_bavail=available, f_frsize=fragment, f_bsize=4096)

    @staticmethod
    def values(samples, name):
        return [sample for sample in samples if sample.name == name]


class MemoryCollectorTests(CollectorFixture):
    def test_bytes_and_distinction_between_used_and_allocated(self):
        (self.paths.proc / "meminfo").write_text(
            "MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\n"
            "Buffers: 50 kB\nCached: 200 kB\nSReclaimable: 80 kB\nShmem: 30 kB\n"
        )
        values = {sample.name: sample.value for sample in MemoryCollector(self.paths).collect()}
        self.assertEqual(values["monix_memory_total_bytes"], 1000 * 1024)
        self.assertEqual(values["monix_memory_available_bytes"], 400 * 1024)
        self.assertEqual(values["monix_memory_used_bytes"], 600 * 1024)
        self.assertEqual(values["monix_memory_allocated_bytes"], 600 * 1024)

    def test_available_can_differ_from_accounted_allocated_memory(self):
        (self.paths.proc / "meminfo").write_text("MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\nCached: 300 kB\n")
        values = {sample.name: sample.value for sample in MemoryCollector(self.paths).collect()}
        self.assertEqual(values["monix_memory_used_bytes"], 600 * 1024)
        self.assertEqual(values["monix_memory_allocated_bytes"], 600 * 1024)
        (self.paths.proc / "meminfo").write_text("MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 450 kB\nCached: 300 kB\n")
        values = {sample.name: sample.value for sample in MemoryCollector(self.paths).collect()}
        self.assertEqual(values["monix_memory_used_bytes"], 550 * 1024)
        self.assertEqual(values["monix_memory_allocated_bytes"], 600 * 1024)

    def test_older_kernel_available_fallback_and_clamp(self):
        (self.paths.proc / "meminfo").write_text("MemTotal: 1000 kB\nMemFree: 100 kB\nCached: 2000 kB\n")
        values = {sample.name: sample.value for sample in MemoryCollector(self.paths).collect()}
        self.assertEqual(values["monix_memory_available_bytes"], 1000 * 1024)
        self.assertEqual(values["monix_memory_used_bytes"], 0)
        self.assertEqual(values["monix_memory_allocated_bytes"], 0)

    def test_missing_invalid_and_partial_meminfo(self):
        self.assertEqual(MemoryCollector(self.paths).collect(), [])
        (self.paths.proc / "meminfo").write_text("MemTotal: nan kB\nMemFree: bad kB\n")
        self.assertEqual(MemoryCollector(self.paths).collect(), [])
        (self.paths.proc / "meminfo").write_text("MemTotal: 1024 kB\n")
        samples = MemoryCollector(self.paths).collect()
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].name, "monix_memory_total_bytes")


class StorageCollectorTests(CollectorFixture):
    def test_sata_nvme_partition_sizes_and_distinct_filesystems(self):
        self.block("sda", "8:0", physical=True, sectors=4000)
        self.block("sda1", "8:1", parent="sda")
        self.block("nvme0n1", "259:0", physical=True, sectors=8000)
        self.block("nvme0n1p1", "259:1", parent="nvme0n1")
        self.mounts(
            "10 1 8:1 / /data rw - ext4 /dev/sda1 rw",
            "11 1 259:1 / / rw - xfs /dev/nvme0n1p1 rw",
        )
        with patch("generated.collectors.storage.collector.os.statvfs", return_value=self.capacity()):
            samples = StorageCollector(self.paths).collect()
        disks = {sample.labels["device"]: sample.value for sample in self.values(samples, "monix_storage_disk_size_bytes")}
        self.assertEqual(disks, {"nvme0n1": 8000 * 512, "sda": 4000 * 512})
        filesystems = self.values(samples, "monix_storage_filesystem_size_bytes")
        self.assertEqual({sample.labels["physical_device"] for sample in filesystems}, {"sda", "nvme0n1"})
        self.assertTrue(all(sample.value == 100 * 4096 for sample in filesystems))
        self.assertEqual(len(self.values(samples, "monix_storage_disk_filesystem_free_bytes")), 2)

    def test_nested_lvm_and_bind_mount_deduplication(self):
        self.block("sda", "8:0", physical=True)
        self.block("sda2", "8:2", parent="sda")
        self.block("dm-0", "253:0")
        self.slaves("dm-0", "sda2")
        self.block("dm-1", "253:1")
        self.slaves("dm-1", "dm-0")
        self.mounts(
            "12 1 253:1 /subdir /bind rw - ext4 /dev/mapper/vg-root rw",
            "11 1 253:1 / / rw - ext4 /dev/mapper/vg-root rw",
        )
        with patch("generated.collectors.storage.collector.os.statvfs", return_value=self.capacity()) as statvfs:
            samples = StorageCollector(self.paths).collect()
        statvfs.assert_called_once_with("/")
        filesystem = self.values(samples, "monix_storage_filesystem_size_bytes")
        self.assertEqual(len(filesystem), 1)
        self.assertEqual(filesystem[0].labels["physical_device"], "sda")
        self.assertEqual(filesystem[0].labels["device"], "/dev/mapper/vg-root")
        aggregate = self.values(samples, "monix_storage_disk_filesystem_size_bytes")
        self.assertEqual(aggregate[0].value, 100 * 4096)

    def test_raid_filesystem_capacity_is_emitted_once_without_disk_free_totals(self):
        for name, number in (("sda", "8:0"), ("sdb", "8:16")):
            self.block(name, number, physical=True)
        self.block("md0", "9:0")
        self.slaves("md0", "sda", "sdb")
        self.mounts("11 1 9:0 / /raid rw - ext4 /dev/md0 rw")
        with patch("generated.collectors.storage.collector.os.statvfs", return_value=self.capacity()):
            samples = StorageCollector(self.paths).collect()
        filesystems = self.values(samples, "monix_storage_filesystem_size_bytes")
        self.assertEqual(len(filesystems), 1)
        self.assertEqual(filesystems[0].labels["physical_device"], "sda,sdb")
        self.assertEqual(len(self.values(samples, "monix_storage_filesystem_device_info")), 2)
        self.assertEqual(self.values(samples, "monix_storage_disk_filesystem_free_bytes"), [])

    def test_incomplete_raid_topology_does_not_assign_total_capacity_to_known_disk(self):
        self.block("sda", "8:0", physical=True)
        self.block("md0", "9:0")
        self.slaves("md0", "sda", "sdb")
        self.mounts("11 1 9:0 / /raid rw - ext4 /dev/md0 rw")
        with patch("generated.collectors.storage.collector.os.statvfs", return_value=self.capacity()):
            samples = StorageCollector(self.paths).collect()
        self.assertEqual(self.values(samples, "monix_storage_filesystem_size_bytes")[0].labels["physical_device"], "sda,unknown")
        self.assertEqual(self.values(samples, "monix_storage_disk_filesystem_free_bytes"), [])

    def test_btrfs_subvolumes_and_multidisk_anonymous_device_ids(self):
        self.block("sda", "8:0", physical=True)
        self.block("sda1", "8:1", parent="sda")
        self.block("sdb", "8:16", physical=True)
        self.block("sdb1", "8:17", parent="sdb")
        devices = self.paths.sys / "fs/btrfs/filesystem-uuid/devices"
        devices.mkdir(parents=True)
        (devices / "1").symlink_to(self.block_paths["sda1"], target_is_directory=True)
        (devices / "2").symlink_to(self.block_paths["sdb1"], target_is_directory=True)
        self.mounts(
            "11 1 0:32 /@ / rw - btrfs /dev/sda1 rw",
            "12 1 0:33 /@home /home rw - btrfs /dev/sdb1 rw",
        )
        with patch("generated.collectors.storage.collector.os.statvfs", return_value=self.capacity()) as statvfs:
            samples = StorageCollector(self.paths).collect()
        statvfs.assert_called_once_with("/")
        filesystems = self.values(samples, "monix_storage_filesystem_size_bytes")
        self.assertEqual(len(filesystems), 1)
        self.assertEqual(filesystems[0].labels["physical_device"], "sda,sdb")
        self.assertEqual(self.values(samples, "monix_storage_disk_filesystem_size_bytes"), [])

    def test_mount_escape_bytes_reserved_space_and_remote_filesystem_filter(self):
        self.block("sda", "8:0", physical=True)
        self.block("sda1", "8:1", parent="sda")
        self.mounts(
            r"11 1 8:1 / /media/My\040Disk rw - ext4 /dev/sda1 rw",
            "12 1 0:20 / /remote rw - nfs server:/volume rw",
            "13 1 0:21 / /tmp rw - tmpfs tmpfs rw",
            "14 1 0:22 / /proc rw - proc proc rw",
        )
        with patch("generated.collectors.storage.collector.os.statvfs", return_value=self.capacity(fragment=1024)) as statvfs:
            samples = StorageCollector(self.paths).collect()
        statvfs.assert_called_once_with("/media/My Disk")
        self.assertEqual(self.values(samples, "monix_storage_filesystem_size_bytes")[0].value, 100 * 1024)
        self.assertEqual(self.values(samples, "monix_storage_filesystem_free_bytes")[0].value, 40 * 1024)
        self.assertEqual(self.values(samples, "monix_storage_filesystem_available_bytes")[0].value, 30 * 1024)
        self.assertEqual(self.values(samples, "monix_storage_filesystem_used_bytes")[0].value, 60 * 1024)

    def test_btrfs_missing_topology_does_not_assume_single_disk_ownership(self):
        self.block("sda", "8:0", physical=True)
        self.block("sda1", "8:1", parent="sda")
        self.mounts("11 1 0:32 /@ / rw - btrfs /dev/sda1 rw")
        with patch("generated.collectors.storage.collector.os.statvfs", return_value=self.capacity()):
            samples = StorageCollector(self.paths).collect()
        self.assertEqual(self.values(samples, "monix_storage_filesystem_size_bytes")[0].labels["physical_device"], "sda")
        self.assertEqual(self.values(samples, "monix_storage_disk_filesystem_size_bytes"), [])

    def test_btrfs_incomplete_topology_does_not_assign_total_to_known_disk(self):
        self.block("sda", "8:0", physical=True)
        self.block("sda1", "8:1", parent="sda")
        devices = self.paths.sys / "fs/btrfs/filesystem-uuid/devices"
        devices.mkdir(parents=True)
        (devices / "1").symlink_to(self.block_paths["sda1"], target_is_directory=True)
        (devices / "2").symlink_to(self.paths.sys / "missing/sdb1", target_is_directory=True)
        self.mounts("11 1 0:32 /@ / rw - btrfs /dev/sda1 rw")
        with patch("generated.collectors.storage.collector.os.statvfs", return_value=self.capacity()):
            samples = StorageCollector(self.paths).collect()
        self.assertEqual(self.values(samples, "monix_storage_filesystem_size_bytes")[0].labels["physical_device"], "sda,unknown")
        self.assertEqual(self.values(samples, "monix_storage_disk_filesystem_size_bytes"), [])

    def test_raw_disks_and_inaccessible_mounts_do_not_fabricate_free_space(self):
        self.block("sda", "8:0", physical=True)
        self.block("sda1", "8:1", parent="sda")
        self.block("loop0", "7:0", physical=True)
        self.mounts("11 1 8:1 / /missing rw - ext4 /dev/sda1 rw")
        with patch("generated.collectors.storage.collector.os.statvfs", side_effect=OSError("unmounted")):
            samples = StorageCollector(self.paths).collect()
        disks = self.values(samples, "monix_storage_disk_size_bytes")
        self.assertEqual(len(disks), 1)
        self.assertEqual(disks[0].labels["device"], "sda")
        self.assertFalse(self.values(samples, "monix_storage_filesystem_size_bytes"))
        self.assertFalse(self.values(samples, "monix_storage_disk_filesystem_free_bytes"))

    def test_missing_sysfs_and_mountinfo(self):
        self.assertEqual(StorageCollector(self.paths).collect(), [])


if __name__ == "__main__":
    unittest.main()
