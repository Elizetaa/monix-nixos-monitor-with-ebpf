/* Contador de eventos perdidos (mapa cheio ou falha de atualização).
 *
 * observador.c lê "lost" com read_percpu() e soma as CPUs: qualquer valor
 * diferente de zero marca a categoria como "partial".
 */
#ifndef MONIX_PERDAS_BPF_H
#define MONIX_PERDAS_BPF_H

struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, __u64);
} lost SEC(".maps");

static __always_inline void count_lost(void)
{
    __u32 key = 0;
    __u64 *value = bpf_map_lookup_elem(&lost, &key);
    if (value)
        *value += 1; /* PERCPU: cada CPU tem a própria cópia, sem corrida. */
}

#endif
