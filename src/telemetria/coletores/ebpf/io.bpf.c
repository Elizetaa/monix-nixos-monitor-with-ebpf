/* I/O por dispositivo e operação. Requer a assinatura moderna (Linux >=5.11). */
#include "vmlinux.h"
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_helpers.h>
#include "metricas.h"
#include "perdas.bpf.h"

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, MONIX_MAX_REQUESTS);
    __type(key, __u64); /* Endereço da request, nunca exposto na API. */
    __type(value, __u64);
} starts SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, MONIX_MAX_DEVICES);
    __type(key, struct io_key);
    __type(value, struct io_counters);
} counters SEC(".maps");

static __always_inline struct io_counters *device_counters(struct request *rq)
{
    __u32 operation = BPF_CORE_READ(rq, cmd_flags) & 0xff;
    if (operation > 1)
        return NULL; /* Flush, discard e outras operações não são R/W. */
    struct gendisk *disk = BPF_CORE_READ(rq, q, disk);
    if (!disk)
        return NULL;
    struct io_key key = {
        .major = BPF_CORE_READ(disk, major),
        .minor = BPF_CORE_READ(disk, first_minor),
        .operation = operation,
    };
    struct io_counters *value = bpf_map_lookup_elem(&counters, &key);
    if (!value) {
        struct io_counters initial = {};
        bpf_map_update_elem(&counters, &key, &initial, BPF_NOEXIST);
        value = bpf_map_lookup_elem(&counters, &key);
        if (!value)
            count_lost();
    }
    return value;
}

SEC("raw_tp/block_rq_issue")
int issued(struct bpf_raw_tracepoint_args *ctx)
{
    struct request *rq = (void *)ctx->args[0];
    struct io_counters *value = device_counters(rq);
    if (!value)
        return 0;
    __sync_fetch_and_add(&value->issued, 1);
    __u64 address = (__u64)rq;
    __u64 now = bpf_ktime_get_ns();
    /* Uma reemissão reinicia a latência: medimos a última emissão. */
    if (bpf_map_update_elem(&starts, &address, &now, BPF_ANY))
        count_lost();
    return 0;
}

SEC("raw_tp/block_rq_complete")
int completed(struct bpf_raw_tracepoint_args *ctx)
{
    struct request *rq = (void *)ctx->args[0];
    struct io_counters *value = device_counters(rq);
    if (!value)
        return 0;
    __u64 bytes = ctx->args[2];
    __sync_fetch_and_add(&value->completions, 1);
    __sync_fetch_and_add(&value->completed_bytes, bytes);
    if (ctx->args[1])
        __sync_fetch_and_add(&value->errors, 1);
    /* O tracepoint roda antes de atualizar __data_len: não encerra parciais. */
    if (bytes >= BPF_CORE_READ(rq, __data_len)) {
        __u64 address = (__u64)rq;
        __u64 *start = bpf_map_lookup_elem(&starts, &address);
        __u64 now = bpf_ktime_get_ns();
        if (start && now >= *start) {
            __sync_fetch_and_add(&value->latency_ns, now - *start);
            __sync_fetch_and_add(&value->latency_samples, 1);
        }
        bpf_map_delete_elem(&starts, &address);
    }
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
