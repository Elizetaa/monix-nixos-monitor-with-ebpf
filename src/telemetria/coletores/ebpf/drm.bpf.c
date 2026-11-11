/* Atividade do gpu_sched (Linux 6.18+), não percentual de utilização da GPU. */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include "metricas.h"

struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, struct gpu_counters);
} counters SEC(".maps");

SEC("raw_tp/drm_sched_job_queue")
int queued(struct bpf_raw_tracepoint_args *ctx)
{
    __u32 key = 0;
    struct gpu_counters *value = bpf_map_lookup_elem(&counters, &key);
    if (value)
        __sync_fetch_and_add(&value->queued, 1);
    return 0;
}

SEC("raw_tp/drm_sched_job_run")
int run(struct bpf_raw_tracepoint_args *ctx)
{
    __u32 key = 0;
    struct gpu_counters *value = bpf_map_lookup_elem(&counters, &key);
    if (value)
        __sync_fetch_and_add(&value->run, 1);
    return 0;
}

SEC("raw_tp/drm_sched_job_done")
int done(struct bpf_raw_tracepoint_args *ctx)
{
    __u32 key = 0;
    struct gpu_counters *value = bpf_map_lookup_elem(&counters, &key);
    if (value)
        __sync_fetch_and_add(&value->done, 1);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
