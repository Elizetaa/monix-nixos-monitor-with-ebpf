/* Restringe os hooks de amostragem ao processo do loader (observador.c).
 *
 * O loader grava o próprio PID na posição 0 de "owner" logo após o load
 * (ver load_collector em observador.c). Até lá o valor é 0 e nenhuma chamada
 * é aceita. Assim, só as leituras provocadas pelo próprio observador
 * (/proc/meminfo, fstatfs) preenchem os mapas, nunca as de outros processos.
 */
#ifndef MONIX_AMOSTRAGEM_BPF_H
#define MONIX_AMOSTRAGEM_BPF_H

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, __u32);
    __type(value, __u32);
} owner SEC(".maps");

static __always_inline bool own_call(void)
{
    __u32 key = 0;
    __u32 *pid = bpf_map_lookup_elem(&owner, &key);
    if (!pid || !*pid)
        return false;
    /* Os 32 bits altos são o TGID, isto é, o PID visto pelo userspace. */
    return (__u32)(bpf_get_current_pid_tgid() >> 32) == *pid;
}

#endif
