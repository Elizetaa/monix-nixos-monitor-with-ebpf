/* CPU busy/idle time from sched_switch, with CO-RE instead of a fixed ABI. */
#include "vmlinux.h"
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_helpers.h>

/* Keep this layout synchronized with cpu_usage.c. Each CPU has one writer. */
struct cpu_counter {
    __u64 sequence;
    __u64 busy_ns;
    __u64 idle_ns;
    __u64 last_timestamp_ns;
    __u32 is_idle;
    __u32 padding;
};

struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, struct cpu_counter);
} cpu_stats SEC(".maps");

SEC("raw_tp/sched_switch")
int on_sched_switch(struct bpf_raw_tracepoint_args *ctx)
{
    /* raw sched_switch args: preempt, prev task, next task, prev_state. */
    struct task_struct *next = (struct task_struct *)ctx->args[2];
    __u32 key = 0;
    struct cpu_counter *counter = bpf_map_lookup_elem(&cpu_stats, &key);
    __u64 now = bpf_ktime_get_ns();
    if (!counter)
        return 0;

    /* Odd means a write is in progress. Userspace compares two snapshots. */
    __sync_fetch_and_add(&counter->sequence, 1);
    if (counter->last_timestamp_ns && now >= counter->last_timestamp_ns) {
        __u64 elapsed = now - counter->last_timestamp_ns;
        if (counter->is_idle)
            counter->idle_ns += elapsed;
        else
            counter->busy_ns += elapsed;
    }
    counter->last_timestamp_ns = now;
    counter->is_idle = BPF_CORE_READ(next, pid) == 0;
    __sync_fetch_and_add(&counter->sequence, 1);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
