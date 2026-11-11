/* O loader provoca meminfo_proc_show(); os valores vêm dos retornos do kernel. */
#include "vmlinux.h"
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include "metricas.h"
#include "amostragem.bpf.h"

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, struct memory_sample);
} samples SEC(".maps");

SEC("fexit/si_meminfo")
int BPF_PROG(read_memory, struct sysinfo *info)
{
    if (!own_call())
        return 0;
    __u64 unit = BPF_CORE_READ(info, mem_unit);
    struct memory_sample sample = {
        .timestamp_ns = bpf_ktime_get_ns(),
        .total_bytes = BPF_CORE_READ(info, totalram) * unit,
        .free_bytes = BPF_CORE_READ(info, freeram) * unit,
        .shared_bytes = BPF_CORE_READ(info, sharedram) * unit,
        .buffer_bytes = BPF_CORE_READ(info, bufferram) * unit,
        .page_size_bytes = unit,
    };
    __u32 key = 0;
    bpf_map_update_elem(&samples, &key, &sample, BPF_ANY);
    return 0;
}

SEC("fexit/si_mem_available")
int BPF_PROG(read_available, long ret)
{
    if (!own_call() || ret < 0)
        return 0;
    __u32 key = 0;
    struct memory_sample *sample = bpf_map_lookup_elem(&samples, &key);
    if (sample && sample->timestamp_ns) {
        sample->available_bytes = (__u64)ret * sample->page_size_bytes;
        sample->available_timestamp_ns = bpf_ktime_get_ns();
    }
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
