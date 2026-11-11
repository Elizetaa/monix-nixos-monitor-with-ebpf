/* Contadores de atividade desde o attach, sem inferir RAM usada por processo. */
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include "metricas.h"

struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, struct page_counters);
} counters SEC(".maps");

static __always_inline int count_pages(unsigned int order, bool allocated)
{
    __u32 key = 0;
    struct page_counters *value = bpf_map_lookup_elem(&counters, &key);
    if (value && order < 32) {
        if (allocated)
            __sync_fetch_and_add(&value->allocated_pages, 1ULL << order);
        else
            __sync_fetch_and_add(&value->freed_pages, 1ULL << order);
    }
    return 0;
}

SEC("raw_tp/mm_page_alloc")
int allocated(struct bpf_raw_tracepoint_args *ctx)
{
    /* page == NULL indica uma tentativa de alocação que falhou. */
    return ctx->args[0] ? count_pages(ctx->args[1], true) : 0;
}

SEC("raw_tp/mm_page_free")
int freed(struct bpf_raw_tracepoint_args *ctx)
{
    return count_pages(ctx->args[1], false);
}

SEC("raw_tp/mm_page_free_batched")
int freed_batched(struct bpf_raw_tracepoint_args *ctx)
{
    return count_pages(0, false);
}

char LICENSE[] SEC("license") = "GPL";
