/* Temperaturas publicadas pelo subsistema térmico, sem ler sysfs. */
#include "vmlinux.h"
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_helpers.h>
#include "metricas.h"
#include "perdas.bpf.h"

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, MONIX_MAX_ZONES);
    __type(key, __u32);
    __type(value, struct thermal_sample);
} samples SEC(".maps");

SEC("raw_tp/thermal_temperature")
int temperature(struct bpf_raw_tracepoint_args *ctx)
{
    struct thermal_zone_device *zone = (void *)ctx->args[0];
    __u32 key = BPF_CORE_READ(zone, id);
    struct thermal_sample sample = {
        .timestamp_ns = bpf_ktime_get_ns(),
        .temperature_millicelsius = BPF_CORE_READ(zone, temperature),
    };
    BPF_CORE_READ_STR_INTO(&sample.type, zone, type);
    if (bpf_map_update_elem(&samples, &key, &sample, BPF_ANY))
        count_lost();
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
