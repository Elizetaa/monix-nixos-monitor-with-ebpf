#include <linux/bpf.h>       /* Tipos e definições fundamentais do eBPF. */
#include <bpf/bpf_helpers.h> /* Helpers e macros, incluindo SEC(). */

/*
 * Define o ponto de anexação do programa.
 *
 * SEC() coloca a função em uma seção específica do arquivo ELF. A libbpf e
 * o bpftool interpretam "kprobe/__x64_sys_execve" como a instrução para
 * anexar o programa à entrada dessa função do kernel.
 *
 * Esse símbolo de execve é usado pelo kernel x86-64 do ambiente WSL 2 do
 * curso e pode ser diferente em outras arquiteturas ou versões do kernel.
 */
SEC("kprobe/__x64_sys_execve")
int trace_exec(void *ctx)
{
    /*
     * Reserva 16 bytes para o nome curto do processo atual: até 15
     * caracteres e o terminador nulo '\0', conforme TASK_COMM_LEN no Linux.
     */
    char comm[16];

    /*
     * Helpers são funções oferecidas pelo kernel aos programas eBPF. Este
     * helper copia para comm o nome curto da tarefa que acionou o Kprobe.
     *
     * O programa é executado no início de execve. Nesse instante, o processo
     * ainda pode conservar o nome anterior, como "bash", antes de assumir o
     * nome do novo executável.
     */
    bpf_get_current_comm(&comm, sizeof(comm));

    /* A mensagem de formato fica armazenada na pilha do programa eBPF. */
    char msg[] = "KPROBE: Processo '%s' executado!\n";

    /*
     * Envia a mensagem ao sistema de tracing do kernel. Enquanto o programa
     * estiver anexado, ela pode ser lida em /sys/kernel/tracing/trace_pipe.
     */
    bpf_trace_printk(msg, sizeof(msg), comm);

    /*
     * Em um Kprobe, retornar zero encerra normalmente a observação e não
     * altera o resultado da chamada execve original.
     */
    return 0;
}

/*
 * Informa a licença ao kernel. A licença GPL permite o uso de helpers
 * disponibilizados somente a programas compatíveis com GPL.
 */
char _license[] SEC("license") = "GPL";
