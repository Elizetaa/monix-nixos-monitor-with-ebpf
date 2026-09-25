#include <linux/bpf.h>       /* Tipos e contexto do eBPF. */
#include <linux/pkt_cls.h>   /* Ações do TC, como TC_ACT_OK e TC_ACT_SHOT. */
#include <linux/if_ether.h>  /* Estrutura do cabeçalho Ethernet. */
#include <linux/in.h>        /* Constantes de protocolos, como IPPROTO_TCP. */
#include <linux/ip.h>        /* Estrutura do cabeçalho IPv4. */
#include <bpf/bpf_helpers.h> /* Helpers e macros, incluindo SEC(). */
#include <bpf/bpf_endian.h>  /* Conversões de ordem de bytes. */

/*
 * SEC("classifier") declara um classificador do Traffic Control. Neste
 * laboratório, ele é anexado ao egress para analisar os pacotes que saem da
 * interface.
 */
SEC("classifier")
int tc_egress_filter(struct __sk_buff *skb)
{
    /*
     * O contexto __sk_buff fornece acesso direto aos bytes do pacote. data
     * aponta para o início e data_end delimita o final da região acessível.
     */
    void *data_end = (void *)(long)skb->data_end;
    void *data     = (void *)(long)skb->data;

    /* Interpreta os primeiros bytes como um cabeçalho Ethernet. */
    struct ethhdr *eth = data;

    /*
     * Prova ao eBPF Verifier que o cabeçalho Ethernet completo está dentro
     * dos limites do pacote antes de acessar seus campos.
     */
    if ((void *)(eth + 1) > data_end) return TC_ACT_OK;

    /*
     * Processa somente IPv4. bpf_ntohs() converte h_proto da ordem de bytes
     * de rede antes da comparação.
     */
    if (bpf_ntohs(eth->h_proto) != ETH_P_IP) return TC_ACT_OK;

    /* Localiza o cabeçalho IPv4 logo depois do cabeçalho Ethernet. */
    struct iphdr *iph = (void *)(eth + 1);

    /* Valida o cabeçalho IPv4 antes de acessar protocol e ihl. */
    if ((void *)(iph + 1) > data_end) return TC_ACT_OK;

    /* A regra da porta 80 interessa somente a pacotes que transportam TCP. */
    if (iph->protocol == IPPROTO_TCP) {
        /*
         * O filtro precisa apenas das portas de origem e destino, os quatro
         * primeiros bytes do cabeçalho TCP. A estrutura local descreve
         * somente esses campos.
         */
        struct tcphdr {
            __be16 source;
            __be16 dest;

        /*
         * O tamanho do cabeçalho IPv4 é variável. iph->ihl informa seu
         * tamanho em palavras de 32 bits; multiplicar por 4 converte esse
         * valor para bytes e localiza corretamente o início do TCP.
         */
        } *tcp = (void *)iph + (iph->ihl * 4);

        /* Prova ao Verifier que as duas portas estão dentro do pacote. */
        if ((void *)(tcp + 1) > data_end) return TC_ACT_OK;

        /*
         * As portas TCP usam ordem de bytes de rede. bpf_htons(80) representa
         * a porta HTTP no mesmo formato armazenado em tcp->dest.
         */
        if (tcp->dest == bpf_htons(80)) {
            /* TC_ACT_SHOT descarta o pacote no caminho de saída. */
            return TC_ACT_SHOT;
        }
    }

    /* TC_ACT_OK permite que todos os demais pacotes continuem normalmente. */
    return TC_ACT_OK;
}

/* Informa ao kernel que o programa utiliza uma licença compatível com GPL. */
char _license[] SEC("license") = "GPL";
