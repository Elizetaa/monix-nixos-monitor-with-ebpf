# Análise dos códigos: Kprobe e BPF LSM

Neste laboratório, dois programas eBPF atuam em momentos relacionados à
execução de processos, mas possuem responsabilidades diferentes:

- o **Kprobe** observa a entrada da função `__x64_sys_execve` e registra um
  evento, sem interferir na operação;
- o **BPF LSM** participa da decisão de segurança do kernel e pode impedir a
  execução de um arquivo.

Essa diferença é determinada pelo tipo do programa, pelo ponto de anexação e
pelo significado de seu valor de retorno.

## 1. Kprobe: observando a execução de processos

O primeiro programa é anexado à entrada de `__x64_sys_execve`, função usada
pelo kernel x86-64 no processamento da chamada de sistema `execve`. Sempre que
esse ponto for alcançado, o programa obtém o nome curto do processo atual e
escreve uma mensagem no sistema de tracing do kernel.

```c
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

/*
 * Define o ponto de anexação do programa.
 *
 * A macro SEC() coloca a função em uma seção específica do arquivo ELF.
 * A libbpf e o bpftool interpretam "kprobe/__x64_sys_execve" como a instrução
 * para anexar o programa à entrada dessa função do kernel.
 *
 * Esse nome é específico da arquitetura x86-64 e pode ser diferente em
 * outras arquiteturas ou versões do kernel.
 */
SEC("kprobe/__x64_sys_execve")
int trace_exec(void *ctx)
{
    /*
     * Reserva 16 bytes para o nome curto do processo atual. No Linux, esse
     * nome possui o limite definido por TASK_COMM_LEN: 15 caracteres e o
     * terminador nulo '\0'.
     */
    char comm[16];

    /*
     * Um programa eBPF não chama livremente funções comuns do kernel. Ele usa
     * helpers autorizados para seu tipo. Este helper copia para comm o nome
     * curto da tarefa que acionou o Kprobe.
     *
     * O programa está no início de execve. Nesse instante, o processo ainda
     * pode conservar o nome do programa anterior, como "bash", antes de ser
     * substituído pelo novo executável. Por isso a mensagem nem sempre exibe
     * o nome do comando que acabou de ser digitado.
     */
    bpf_get_current_comm(&comm, sizeof(comm));

    /*
     * A mensagem de formato fica na pilha do programa eBPF. O marcador %s
     * será substituído pelo conteúdo de comm.
     */
    char msg[] = "KPROBE: Processo '%s' executado!\n";

    /*
     * Envia a mensagem ao sistema de tracing do kernel. Ela pode ser lida em
     * /sys/kernel/tracing/trace_pipe enquanto o programa estiver anexado.
     */
    bpf_trace_printk(msg, sizeof(msg), comm);

    /*
     * O retorno zero encerra normalmente este programa. Em um Kprobe, esse
     * valor não autoriza nem bloqueia execve: o programa apenas observa o
     * evento e a execução original continua.
     */
    return 0;
}

/*
 * Informa ao kernel a licença do programa. A licença GPL permite o uso de
 * helpers que o kernel disponibiliza somente a programas compatíveis com GPL.
 */
char _license[] SEC("license") = "GPL";
```

### Fluxo do Kprobe

```text
um processo chama execve
          ↓
o kernel entra em __x64_sys_execve
          ↓
o Kprobe executa trace_exec
          ↓
bpf_get_current_comm obtém o nome curto da tarefa
          ↓
bpf_trace_printk registra a mensagem
          ↓
execve continua normalmente
```

O parâmetro `ctx` representa o contexto oferecido ao Kprobe. Este exemplo não
precisa consultar seus registradores porque utiliza
`bpf_get_current_comm()` para obter a informação desejada.

## 2. BPF LSM: impedindo a execução do curl

O segundo programa é anexado ao hook LSM `bprm_check_security`. Esse hook é
consultado durante a preparação da execução de um arquivo. Diferentemente do
Kprobe, seu retorno participa da decisão de segurança: zero permite que a
operação prossiga e um erro negativo pode negá-la.

```c
/*
 * vmlinux.h descreve tipos do kernel em execução a partir de suas informações
 * BTF. Neste laboratório, ele é gerado pelo bpftool durante make setup.
 */
#include <vmlinux.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>

/*
 * EPERM significa "operação não permitida". As interfaces do kernel usam o
 * valor negativo do código para representar uma falha: -EPERM.
 */
#define EPERM 1

/*
 * Anexa o programa ao hook bprm_check_security do Linux Security Module.
 * Esse hook é chamado antes de o kernel concluir a execução de um binário.
 *
 * BPF_PROG declara a função com a assinatura esperada pelo BPF LSM. O
 * parâmetro bprm aponta para uma struct linux_binprm, que reúne informações
 * sobre o arquivo que está sendo preparado para execução.
 */
SEC("lsm/bprm_check_security")
int BPF_PROG(restrict_execution, struct linux_binprm *bprm)
{
    /*
     * Reserva um buffer local para receber o caminho do executável. Apesar do
     * nome comm usado neste exemplo, o conteúdo copiado é um caminho de
     * arquivo, como /usr/bin/curl, e não o nome curto da tarefa.
     */
    char comm[128];

    /*
     * bprm->filename aponta para memória pertencente ao kernel. O helper faz
     * uma cópia segura da string para a pilha do programa eBPF, respeitando o
     * tamanho do buffer e incluindo o terminador nulo quando houver espaço.
     */
    bpf_probe_read_kernel_str(comm, sizeof(comm), bprm->filename);

    /*
     * Compara os 13 primeiros caracteres do caminho com "/usr/bin/curl".
     * Quando o resultado é zero, os trechos comparados são iguais e a regra
     * de bloqueio deve ser aplicada.
     *
     * Como esta é uma comparação de prefixo, o exemplo também corresponderia
     * a um caminho iniciado pelos mesmos 13 caracteres, como
     * /usr/bin/curl-extra. Em uma política de produção, seria necessário
     * conferir também o final exato da string.
     */
    if (bpf_strncmp(comm, 13, "/usr/bin/curl") == 0) {
        char msg[] = "LSM: Execucao do curl bloqueada!\n";

        /* Registra no trace_pipe que a regra de segurança foi acionada. */
        bpf_trace_printk(msg, sizeof(msg));

        /*
         * Retorna o erro -EPERM. Como este é um hook LSM, o kernel interpreta
         * o retorno como uma negação e impede a execução do arquivo.
         */
        return -EPERM;
    }

    /* Qualquer outro caminho é permitido por esta política. */
    return 0;
}

/* Declara a licença GPL do programa para o kernel. */
char _license[] SEC("license") = "GPL";
```

### Fluxo do BPF LSM

```text
um processo tenta executar um arquivo
               ↓
o kernel prepara struct linux_binprm
               ↓
o hook bprm_check_security chama restrict_execution
               ↓
o programa copia e compara bprm->filename
          ┌─────┴─────┐
          ↓           ↓
 /usr/bin/curl      outro caminho
          ↓           ↓
 retorna -EPERM     retorna 0
          ↓           ↓
execução negada  execução permitida
```

## 3. Comparando os programas

| Característica | Kprobe | BPF LSM |
|---|---|---|
| Seção ELF | `kprobe/__x64_sys_execve` | `lsm/bprm_check_security` |
| Finalidade | Observabilidade | Aplicação de política de segurança |
| Informação usada | Nome curto da tarefa atual | Caminho do arquivo a executar |
| Helper principal | `bpf_get_current_comm` | `bpf_probe_read_kernel_str` |
| Significado de `return 0` | Encerra a observação normalmente | Permite que a operação prossiga |
| Pode bloquear `execve`? | Não | Sim, retornando um erro negativo |

Os dois programas são acionados durante o processo de execução, mas não são
intercambiáveis. O contrato do tipo eBPF determina quais informações e helpers
estão disponíveis e qual efeito o valor retornado terá sobre o kernel.

## 4. Limitações intencionais do exemplo

Os programas foram mantidos pequenos para destacar a diferença entre observar
e impedir uma operação. Em uma aplicação de produção, seria importante:

- tratar possíveis falhas dos helpers;
- comparar o caminho completo, e não somente seu prefixo;
- considerar outros caminhos, links e formas de identificar o executável;
- preferir mecanismos de eventos mais adequados a grandes volumes no lugar de
  `bpf_trace_printk`, que é voltado principalmente a depuração;
- verificar a disponibilidade dos hooks e helpers no kernel utilizado.
