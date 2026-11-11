/* fstatfs() só provoca a operação; capacidade é lida aqui, no retorno do VFS. */
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
    __type(value, struct filesystem_sample);
} samples SEC(".maps");

SEC("fexit/fd_statfs")
int BPF_PROG(read_filesystem, unsigned int fd, struct kstatfs *stats, int ret)
{
    if (!own_call())
        return 0;
    struct filesystem_sample sample = {
        .timestamp_ns = bpf_ktime_get_ns(),
        .error = ret,
    };
    if (!ret) {
        __u64 unit = BPF_CORE_READ(stats, f_frsize);
        if (!unit)
            unit = BPF_CORE_READ(stats, f_bsize);
        sample.total_bytes = BPF_CORE_READ(stats, f_blocks) * unit;
        sample.free_bytes = BPF_CORE_READ(stats, f_bfree) * unit;
        sample.available_bytes = BPF_CORE_READ(stats, f_bavail) * unit;
        BPF_CORE_READ_INTO(&sample.fsid, stats, f_fsid.val);
        if (!unit || sample.free_bytes > sample.total_bytes || sample.available_bytes > sample.free_bytes)
            sample.error = -34; /* ERANGE: nunca publica capacidades inválidas. */
    }
    __u32 key = 0;
    bpf_map_update_elem(&samples, &key, &sample, BPF_ANY);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
