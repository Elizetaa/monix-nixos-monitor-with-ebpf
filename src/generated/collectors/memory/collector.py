"""RAM snapshots from procfs, expressed in bytes.

``used`` is total minus MemAvailable, which estimates memory that cannot be
allocated without swapping. ``allocated`` excludes free memory and reclaimable
buffers/cache, while retaining shared memory; it is an accounting estimate,
not a sum of process RSS (which would count shared pages more than once).
"""

import math

from ...common.metrics import Sample
from ...common.paths import SystemPaths, read_text


class MemoryCollector:
    def __init__(self, paths: SystemPaths):
        self.paths = paths

    def collect(self) -> list[Sample]:
        values: dict[str, float] = {}
        for line in read_text(self.paths.proc / "meminfo").splitlines():
            key, separator, raw = line.partition(":")
            fields = raw.split()
            if not separator or not fields:
                continue
            try:
                value = float(fields[0])
            except ValueError:
                continue
            if not math.isfinite(value) or value < 0:
                continue
            if len(fields) > 1:
                if fields[1] != "kB":
                    continue
                value *= 1024
            values[key] = value

        total = values.get("MemTotal")
        if total is None or total <= 0:
            return []
        samples = [Sample("monix_memory_total_bytes", "Total physical RAM in bytes.", total)]

        def clamp(value: float) -> float:
            return min(total, max(0.0, value))

        allocated = None
        if "MemFree" in values:
            samples.append(Sample("monix_memory_free_bytes", "Completely unused RAM in bytes; excludes reclaimable buffers/cache.", clamp(values["MemFree"])))
            allocated = clamp(
                total
                - values["MemFree"]
                - values.get("Buffers", 0)
                - values.get("Cached", 0)
                - values.get("SReclaimable", 0)
                + values.get("Shmem", 0)
            )
        available = values.get("MemAvailable")
        if available is None and allocated is not None:
            available = total - allocated
        if available is not None:
            available = clamp(available)
            samples.extend([
                Sample("monix_memory_available_bytes", "RAM available for allocation without swapping, in bytes.", available),
                Sample("monix_memory_used_bytes", "Total RAM minus available RAM, in bytes.", total - available),
            ])
        if allocated is not None:
            samples.append(Sample(
                "monix_memory_allocated_bytes",
                "RAM excluding free memory and reclaimable buffers/cache, including shared memory, in bytes.",
                allocated,
            ))
        return samples
