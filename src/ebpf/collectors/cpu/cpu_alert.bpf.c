/*
 * cpuAlertEbpf.c
 * ----------------
 * Programa eBPF (lado kernel) que acompanha, por CPU, quanto tempo é gasto
 * "ocupado" (rodando um processo real) vs "ocioso" (rodando a tarefa idle,
 * PID 0), usando o tracepoint sched:sched_switch — que dispara a cada troca
 * de contexto.
 *
 * Isto é o tipo de coisa para a qual eBPF realmente serve: capturar um
 * evento de altíssima frequência do kernel de forma eficiente, sem copiar
 * dados para o espaço de usuário a cada ocorrência.
 *
 * O cálculo de % e a decisão de disparar o "hook" (a ação quando o uso
 * ultrapassa um limite) ficam no programa em userspace (cpu_alert.c),
 * que lê os contadores periodicamente. Isso é intencional: manter a
 * política/decisão fora do kernel deixa o programa BPF simples e o
 * limite configurável sem recompilar nada.
 */

#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

/* Layout do tracepoint sched:sched_switch neste kernel — conferido em
 * /sys/kernel/tracing/events/sched/sched_switch/format */
struct trace_event_raw_sched_switch {
    unsigned short common_type;
    unsigned char  common_flags;
    unsigned char  common_preempt_count;
    int            common_pid;

    char prev_comm[16];
    int  prev_pid;
    int  prev_prio;
    long prev_state;
    char next_comm[16];
    int  next_pid;
    int  next_prio;
};

#define IDX_BUSY      0
#define IDX_IDLE      1
#define IDX_LAST_TS   2
#define IDX_WAS_IDLE  3

/* PERCPU_ARRAY: cada CPU enxerga sua PRÓPRIA cópia do valor de cada
 * índice. Não há necessidade de locks nem de indexar manualmente por CPU:
 * quando o programa roda na CPU X, bpf_map_lookup_elem já devolve o slot
 * daquela CPU automaticamente. */
struct {
    __uint(type, BPF_MAP_TYPE_PERCPU_ARRAY);
    __type(key, __u32);
    __type(value, __u64);
    __uint(max_entries, 4);
} cpu_stats SEC(".maps");

SEC("tp/sched/sched_switch")
int on_sched_switch(struct trace_event_raw_sched_switch *ctx)
{
    __u32 key;
    __u64 *val;
    __u64 now = bpf_ktime_get_ns();

    key = IDX_LAST_TS;
    val = bpf_map_lookup_elem(&cpu_stats, &key);
    if (!val)
        return 0;
    __u64 last_ts = *val;

    key = IDX_WAS_IDLE;
    __u64 *was_idle_p = bpf_map_lookup_elem(&cpu_stats, &key);
    if (!was_idle_p)
        return 0;
    __u64 was_idle = *was_idle_p;

    /* Contabiliza o tempo desde a última troca de contexto NESTA cpu no
     * balde certo (ocupado ou ocioso), de acordo com o que estava rodando. */
    if (last_ts != 0) {
        __u64 delta = now - last_ts;
        key = was_idle ? IDX_IDLE : IDX_BUSY;
        val = bpf_map_lookup_elem(&cpu_stats, &key);
        if (val)
            __sync_fetch_and_add(val, delta);
    }

    /* Atualiza o timestamp e marca se a tarefa que PASSA a rodar agora é
     * a idle (pid 0) — isso decide o balde do próximo intervalo. */
    key = IDX_LAST_TS;
    val = bpf_map_lookup_elem(&cpu_stats, &key);
    if (val)
        *val = now;

    key = IDX_WAS_IDLE;
    val = bpf_map_lookup_elem(&cpu_stats, &key);
    if (val)
        *val = (ctx->next_pid == 0) ? 1 : 0;

    return 0;
}

char LICENSE[] SEC("license") = "GPL";