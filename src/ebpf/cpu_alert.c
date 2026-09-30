/*
 * cpuAlert.c
 * -----------
 * Carrega cpu_alert.bpf.o, anexa ao tracepoint sched_switch, e a cada
 * intervalo lê os contadores (preenchidos pelo programa eBPF) para
 * calcular o uso de CPU agregado de todos os núcleos. Quando o uso
 * ultrapassa o limite (%) passado por linha de comando, dispara o HOOK.
 *
 * Compilação:
 *   gcc -O2 -Wall -o cpu_alert cpu_alert.c -lbpf
 *
 * Uso (precisa de root / CAP_BPF+CAP_SYS_ADMIN para carregar o programa):
 *   sudo ./cpu_alert 80        # alerta quando uso > 80%, checando a cada 1s
 *   sudo ./cpu_alert 80 2      # mesma coisa, checando a cada 2s
 *
 * Pré-requisito: compilar cpu_alert.bpf.c antes (veja o outro arquivo).
 */

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <signal.h>
#include <bpf/libbpf.h>
#include <bpf/bpf.h>

#define IDX_BUSY      0
#define IDX_IDLE      1
#define IDX_LAST_TS   2
#define IDX_WAS_IDLE  3

static volatile sig_atomic_t running = 1;
static void handle_stop(int sig) { (void)sig; running = 0; }

/* ------------------------------------------------------------------ */
/* O HOOK: isto é o que roda quando o uso de CPU ultrapassa o limite.  */
/* Troque o corpo desta função pela ação que você realmente quiser:    */
/* rodar um script, chamar um webhook, matar/renicear um processo,     */
/* mandar um sinal para outro programa, escrever num log estruturado,  */
/* etc.                                                                 */
/* ------------------------------------------------------------------ */
static void hook_cpu_above_threshold(double usage_percent, double threshold)
{
    fprintf(stderr, ">>> [HOOK] Uso de CPU em %.1f%% (limite: %.1f%%) <<<\n",
            usage_percent, threshold);

    /* Exemplo de ação real (comentado):
     * system("/caminho/para/script-de-alerta.sh");
     */
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "Uso: %s <limite_percentual> [intervalo_segundos]\n", argv[0]);
        fprintf(stderr, "Exemplo: %s 80 1\n", argv[0]);
        return 1;
    }
    double threshold = atof(argv[1]);
    int interval_sec = (argc > 2) ? atoi(argv[2]) : 1;

    signal(SIGINT, handle_stop);
    signal(SIGTERM, handle_stop);

    struct bpf_object *obj = bpf_object__open_file("cpu_alert.bpf.o", NULL);
    if (!obj) {
        fprintf(stderr, "Erro ao abrir cpu_alert.bpf.o (ele existe neste diretório?)\n");
        return 1;
    }

    if (bpf_object__load(obj)) {
        fprintf(stderr, "Erro ao carregar o objeto BPF no kernel.\n");
        fprintf(stderr, "Precisa rodar como root / com CAP_BPF + CAP_SYS_ADMIN.\n");
        bpf_object__close(obj);
        return 1;
    }

    struct bpf_program *prog = bpf_object__find_program_by_name(obj, "on_sched_switch");
    if (!prog) {
        fprintf(stderr, "Programa on_sched_switch não encontrado no objeto.\n");
        bpf_object__close(obj);
        return 1;
    }

    struct bpf_link *link = bpf_program__attach(prog);
    if (!link) {
        fprintf(stderr, "Erro ao anexar ao tracepoint sched_switch.\n");
        bpf_object__close(obj);
        return 1;
    }

    int map_fd = bpf_object__find_map_fd_by_name(obj, "cpu_stats");
    if (map_fd < 0) {
        fprintf(stderr, "Mapa cpu_stats não encontrado.\n");
        bpf_link__destroy(link);
        bpf_object__close(obj);
        return 1;
    }

    int ncpus = libbpf_num_possible_cpus();
    if (ncpus <= 0) ncpus = 1;

    unsigned long long *busy_vals = calloc(ncpus, sizeof(unsigned long long));
    unsigned long long *idle_vals = calloc(ncpus, sizeof(unsigned long long));
    unsigned long long *zeros     = calloc(ncpus, sizeof(unsigned long long));

    printf("Monitorando uso de CPU via eBPF (limite: %.1f%%, intervalo: %ds).\n",
           threshold, interval_sec);
    printf("Ctrl+C para sair.\n\n");

    while (running) {
        sleep(interval_sec);
        if (!running) break;

        __u32 key;

        key = IDX_BUSY;
        bpf_map_lookup_elem(map_fd, &key, busy_vals);
        key = IDX_IDLE;
        bpf_map_lookup_elem(map_fd, &key, idle_vals);

        unsigned long long busy_total = 0, idle_total = 0;
        for (int i = 0; i < ncpus; i++) {
            busy_total += busy_vals[i];
            idle_total += idle_vals[i];
        }

        /* Zera os contadores para a próxima janela de medição. */
        key = IDX_BUSY;
        bpf_map_update_elem(map_fd, &key, zeros, BPF_ANY);
        key = IDX_IDLE;
        bpf_map_update_elem(map_fd, &key, zeros, BPF_ANY);

        unsigned long long total = busy_total + idle_total;
        double usage_percent = total ? (100.0 * (double)busy_total / (double)total) : 0.0;

        printf("Uso de CPU: %5.1f%%\n", usage_percent);

        if (usage_percent > threshold)
            hook_cpu_above_threshold(usage_percent, threshold);
    }

    free(busy_vals);
    free(idle_vals);
    free(zeros);
    bpf_link__destroy(link);
    bpf_object__close(obj);
    return 0;
}