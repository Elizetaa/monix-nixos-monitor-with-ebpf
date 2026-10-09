"""Physical disk sizes and capacity of mounted local block filesystems.

Disk size is a hardware capacity, whereas free space belongs to a filesystem.
Only filesystems backed by exactly one physical disk contribute to disk-level
filesystem capacity totals. Raw disks, unmounted partitions and RAID therefore
do not acquire fabricated free-space values. A filesystem spanning several
disks is emitted once with a comma-separated ``physical_device`` label.
Bind mounts and Btrfs subvolumes are deduplicated; their representative mount
prefers a filesystem-root mount, then the shortest mountpoint. Remote and
pseudo filesystems are excluded before statvfs to avoid network I/O.
"""

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ...common.metrics import Sample
from ...common.paths import SystemPaths, read_number, read_text


_LOCAL_FILESYSTEMS = frozenset({
    "ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "vfat", "exfat",
    "ntfs", "ntfs3", "reiserfs", "jfs", "bcachefs", "iso9660", "udf",
})
_NONPHYSICAL_PREFIXES = ("loop", "dm-", "md", "zram", "ram")
_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")


def _unescape(value: str) -> str:
    return _MOUNT_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


@dataclass(frozen=True)
class _Mount:
    device_id: str
    root: str
    mountpoint: str
    fstype: str
    source: str


class StorageCollector:
    def __init__(self, paths: SystemPaths):
        self.paths = paths

    def _blocks(self) -> dict[str, Path]:
        try:
            return {path.name: path for path in sorted((self.paths.sys / "class/block").iterdir())}
        except OSError:
            return {}

    @staticmethod
    def _physical(blocks: dict[str, Path]) -> dict[str, dict[str, str]]:
        physical: dict[str, dict[str, str]] = {}
        for name, path in blocks.items():
            if name.startswith(_NONPHYSICAL_PREFIXES) or (path / "partition").exists():
                continue
            if not (path / "device").exists():
                continue
            physical[name] = {
                "device": name,
                "model": read_text(path / "device/model").strip(),
                "serial": read_text(path / "device/serial").strip(),
            }
        return physical

    def _block_by_id(self, device_id: str, blocks: dict[str, Path]) -> str | None:
        link = self.paths.sys / "dev/block" / device_id
        if link.exists():
            name = link.resolve().name
            if name in blocks:
                return name
        for name, path in blocks.items():
            if read_text(path / "dev").strip() == device_id:
                return name
        return None

    @staticmethod
    def _source_block(source: str, blocks: dict[str, Path]) -> str | None:
        # Btrfs mountinfo can carry an anonymous 0:N device number. Resolve its
        # source through sysfs names without requiring the /dev path to exist.
        name = Path(source).name
        if name in blocks:
            return name
        if source.startswith("/dev/"):
            name = Path(source).resolve().name
            if name in blocks:
                return name
        return None

    def _physical_backing(
        self, name: str, blocks: dict[str, Path], physical: dict[str, dict[str, str]],
        visited: set[str] | None = None,
    ) -> set[str]:
        visited = set() if visited is None else visited
        if name in visited:
            return set()
        if name not in blocks:
            return {"unknown"}
        visited.add(name)
        if name in physical:
            return {name}
        path = blocks[name]
        try:
            slaves = list((path / "slaves").iterdir())
        except OSError:
            slaves = []
        if slaves:
            result: set[str] = set()
            for slave in slaves:
                result.update(self._physical_backing(slave.name, blocks, physical, visited))
            return result
        if (path / "partition").exists():
            parent = path.resolve().parent.name
            if parent not in blocks:
                # Also supports sysfs snapshots where class entries are copied
                # instead of retaining their original symlink topology.
                candidates = [disk for disk in blocks if re.fullmatch(re.escape(disk) + r"p?\d+", name)]
                parent = max(candidates, key=len) if candidates else ""
            return self._physical_backing(parent, blocks, physical, visited)
        return {"unknown"}

    def _btrfs_groups(self, blocks: dict[str, Path]) -> dict[str, set[str]]:
        groups: dict[str, set[str]] = {}
        try:
            entries = list((self.paths.sys / "fs/btrfs").iterdir())
        except OSError:
            return groups
        for entry in entries:
            try:
                devices = list((entry / "devices").iterdir())
            except OSError:
                continue
            names: set[str] = set()
            for device in devices:
                name = device.resolve().name
                if name not in blocks:
                    name = self._block_by_id(read_text(device / "dev").strip(), blocks)
                names.add(name or "unknown")
            if names:
                groups[entry.name] = names
        return groups

    def _mounts(self) -> list[_Mount]:
        result = []
        for line in read_text(self.paths.proc / "self/mountinfo").splitlines():
            before, separator, after = line.partition(" - ")
            left, right = before.split(), after.split()
            if not separator or len(left) < 6 or len(right) < 3:
                continue
            if right[0] not in _LOCAL_FILESYSTEMS:
                continue
            result.append(_Mount(left[2], _unescape(left[3]), _unescape(left[4]), right[0], _unescape(right[1])))
        return sorted(result, key=lambda mount: (mount.root != "/", len(mount.mountpoint), mount.mountpoint))

    def collect(self) -> list[Sample]:
        blocks = self._blocks()
        physical = self._physical(blocks)
        samples: list[Sample] = []
        for name, labels in physical.items():
            samples.append(Sample("monix_storage_disk_info", "Physical disk identity, independent of available capacity readings.", 1, labels))
            sectors = read_number(blocks[name] / "size")
            if sectors is not None and math.isfinite(sectors) and sectors >= 0:
                samples.append(Sample("monix_storage_disk_size_bytes", "Physical disk capacity in bytes (512-byte sectors).", sectors * 512, labels))

        for name, path in blocks.items():
            if not (path / "partition").exists():
                continue
            backing = self._physical_backing(name, blocks, physical)
            labels = {"device": name, "physical_device": ",".join(sorted(backing)) or "unknown"}
            samples.append(Sample("monix_storage_partition_info", "Block partition identity and physical backing, including unmounted partitions.", 1, labels))
            sectors = read_number(path / "size")
            if sectors is not None and math.isfinite(sectors) and sectors >= 0:
                samples.append(Sample("monix_storage_partition_size_bytes", "Partition capacity in bytes (512-byte sectors).", sectors * 512, labels))

        btrfs_groups = self._btrfs_groups(blocks)
        seen: set[tuple[str, str]] = set()
        totals: dict[str, list[int]] = {}
        for mount in self._mounts():
            name = self._block_by_id(mount.device_id, blocks)
            if name is None and mount.fstype == "btrfs":
                name = self._source_block(mount.source, blocks)
            backing = self._physical_backing(name, blocks, physical) if name else set()
            identity = (mount.fstype, mount.device_id)
            ownership_known = True
            if mount.fstype == "btrfs":
                ownership_known = False
                for fsid, names in btrfs_groups.items():
                    if name in names:
                        identity = ("btrfs", fsid)
                        backing = set().union(*(self._physical_backing(member, blocks, physical) for member in names))
                        ownership_known = "unknown" not in backing
                        break
                else:
                    if name:
                        identity = ("btrfs", name)
            if identity in seen:
                continue
            # A local filesystem with unknown backing remains visible, but is
            # not attributed to a physical disk without positive evidence.
            try:
                stats = os.statvfs(mount.mountpoint)
            except OSError:
                continue
            fragment = stats.f_frsize or stats.f_bsize
            if fragment <= 0:
                continue
            size = max(0, stats.f_blocks) * fragment
            free = min(size, max(0, stats.f_bfree) * fragment)
            available = min(free, max(0, stats.f_bavail) * fragment)
            seen.add(identity)
            labels = {
                "device": mount.source,
                "physical_device": ",".join(sorted(backing)) or "unknown",
                "mountpoint": mount.mountpoint,
                "fstype": mount.fstype,
            }
            for field, value in (("size", size), ("free", free), ("available", available), ("used", size - free)):
                samples.append(Sample(f"monix_storage_filesystem_{field}_bytes", f"Mounted local filesystem {field} space in bytes.", value, labels))
            exclusive = ownership_known and len(backing) == 1 and backing <= physical.keys()
            samples.append(Sample("monix_storage_filesystem_exclusive", "1 when the full filesystem is known to be backed exclusively by one physical disk, otherwise 0.", float(exclusive), labels))
            for disk in sorted(backing & physical.keys()):
                samples.append(Sample("monix_storage_filesystem_device_info", "Mapping from mounted local filesystem to physical disk.", 1, {**labels, "physical_device": disk}))
            if exclusive:
                disk = next(iter(backing))
                total = totals.setdefault(disk, [0, 0, 0])
                for index, value in enumerate((size, free, available)):
                    total[index] += value
        for disk, values in sorted(totals.items()):
            for field, value in zip(("size", "free", "available"), values):
                samples.append(Sample(f"monix_storage_disk_filesystem_{field}_bytes", f"Sum of unique mounted filesystems exclusively backed by this disk: {field} bytes.", value, physical[disk]))
        return samples
