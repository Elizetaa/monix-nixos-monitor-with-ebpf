/*
 * vmlinux.h descreve os tipos do kernel em execução a partir de suas
 * informações BTF. Neste laboratório, ele é gerado durante make setup.
 */
#include <vmlinux.h>
#include <bpf/bpf_helpers.h> /* Helpers e macros básicos do eBPF. */
#include <bpf/bpf_tracing.h> /* Macro BPF_PROG usada pelo programa LSM. */

/*
 * EPERM significa "operação não permitida". O valor negativo -EPERM é
 * retornado ao kernel para indicar que a operação foi negada.
 */
#define EPERM 1

/*
 * Anexa o programa ao hook bprm_check_security do Linux Security Module.
 * Esse hook é consultado antes de o kernel concluir a execução de um arquivo.
 *
 * BPF_PROG declara a assinatura esperada pelo BPF LSM. O parâmetro bprm aponta
 * para uma struct linux_binprm, que contém informações sobre o arquivo que
 * está sendo preparado para execução.
 */
SEC("lsm/bprm_check_security")
int BPF_PROG(restrict_execution, struct linux_binprm *bprm)
{
    /*
     * Reserva um buffer para o caminho do executável. Apesar do nome comm
     * usado neste exemplo, o conteúdo será um caminho como /usr/bin/curl, e
     * não o nome curto da tarefa.
     */
    char comm[128];

    /*
     * bprm->filename aponta para memória do kernel. Este helper copia a string
     * de forma segura para a pilha do programa, respeitando o tamanho de comm.
     */
    bpf_probe_read_kernel_str(comm, sizeof(comm), bprm->filename);

    /*
     * Compara os 13 primeiros caracteres do caminho com "/usr/bin/curl".
     * Resultado zero significa que os trechos comparados são iguais.
     *
     * Como a comparação verifica um prefixo, também corresponderia a um caminho
     * como /usr/bin/curl-extra. Uma política de produção deveria conferir o
     * final exato da string.
     */
    if (bpf_strncmp(comm, 13, "/usr/bin/curl") == 0) {
        char msg[] = "LSM: Execucao do curl bloqueada!\n";

        /* Registra no trace_pipe que a regra de segurança foi acionada. */
        bpf_trace_printk(msg, sizeof(msg));

        /* O erro negativo faz o hook LSM impedir a execução do arquivo. */
        return -EPERM;
    }

    /* Zero permite que qualquer outro arquivo prossiga normalmente. */
    return 0;
}

/* Declara ao kernel a licença GPL do programa. */
char _license[] SEC("license") = "GPL";
