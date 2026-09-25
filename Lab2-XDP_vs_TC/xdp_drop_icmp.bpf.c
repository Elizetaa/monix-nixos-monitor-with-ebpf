#include <linux/bpf.h>       /* Tipos, contexto e ações do eBPF/XDP. */
#include <linux/if_ether.h>  /* Estrutura do cabeçalho Ethernet. */
#include <linux/in.h>        /* Constantes de protocolos, como IPPROTO_ICMP. */
#include <linux/ip.h>        /* Estrutura do cabeçalho IPv4. */
#include <bpf/bpf_helpers.h> /* Helpers e macros, incluindo SEC(). */
#include <bpf/bpf_endian.h>  /* Conversões de ordem de bytes. */

/*
 * SEC("xdp") declara um programa executado na entrada dos pacotes. O contexto
 * xdp_md fornece, entre outras informações, os limites da memória do pacote.
 */
SEC("xdp")
int xdp_firewall(struct xdp_md *ctx)
{
    /*
     * ctx armazena os endereços como inteiros. As conversões produzem
     * ponteiros: data aponta para o primeiro byte do pacote e data_end para o
     * limite final de sua região de memória.
     */
    void *data_end = (void *)(long)ctx->data_end;
    void *data     = (void *)(long)ctx->data;

    /* Interpreta os primeiros bytes como um cabeçalho Ethernet. */
    struct ethhdr *eth = data;

    /*
     * Antes de acessar os campos, prova ao eBPF Verifier que o cabeçalho
     * completo está dentro do pacote. Como eth aponta para struct ethhdr,
     * eth + 1 indica o primeiro byte depois desse cabeçalho.
     */
    if ((void *)(eth + 1) > data_end) return XDP_PASS;

    /*
     * Processa somente IPv4. h_proto usa ordem de bytes de rede, portanto
     * bpf_ntohs() converte o valor antes de compará-lo com ETH_P_IP.
     */
    if (bpf_ntohs(eth->h_proto) != ETH_P_IP) return XDP_PASS;

    /* O cabeçalho IPv4 começa logo depois do cabeçalho Ethernet. */
    struct iphdr *iph = (void *)(eth + 1);

    /*
     * Prova ao Verifier que existe pelo menos um cabeçalho IPv4 básico
     * completo antes de acessar iph->protocol.
     */
    if ((void *)(iph + 1) > data_end) return XDP_PASS;

    /*
     * IPPROTO_ICMP identifica pacotes como os utilizados pelo ping. XDP_DROP
     * encerra o processamento e descarta o pacote antes que ele avance pela
     * pilha de rede do kernel.
     */
    if (iph->protocol == IPPROTO_ICMP) {
        return XDP_DROP;
    }

    /* Libera todos os pacotes que não correspondam à regra de bloqueio. */
    return XDP_PASS;
}

/* Informa ao kernel que o programa utiliza uma licença compatível com GPL. */
char _license[] SEC("license") = "GPL";
