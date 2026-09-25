# Análise Prática: XDP vs TC no eBPF

Neste laboratório, comparamos dois programas eBPF reais: um firewall de entrada escrito em **XDP** e um filtro de saída escrito em **TC**. 

Embora a sintaxe de ambos pareça similar (pois ambos acessam a memória do pacote diretamente para máxima performance), os pontos de anexo, os contextos e as ações de retorno revelam as diferenças fundamentais entre atuar no driver da placa de rede (XDP) e atuar na pilha de rede do Kernel (TC).

---

## 1. O Programa XDP: Bloqueando Pings (ICMP) no Ingress

Este programa XDP é anexado diretamente ao driver da placa de rede. Seu objetivo é descartar (dropar) qualquer pacote ICMP (como o comando `ping`) antes mesmo que o kernel do Linux saiba que ele chegou.

```c
#include <linux/bpf.h>
#include <linux/if_ether.h>
#include <linux/in.h>
#include <linux/ip.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

// 1. Define onde este programa será anexado (Hook XDP)
SEC("xdp")
int xdp_firewall(struct xdp_md *ctx)
{
    // 2. Extraindo os ponteiros de memória do pacote bruto
    // ctx->data aponta para o primeiro byte do pacote.
    // ctx->data_end aponta para o final do pacote na memória.
    void *data_end = (void *)(long)ctx->data_end;
    void *data     = (void *)(long)ctx->data;

    // 3. Lendo o Cabeçalho Ethernet (Camada 2 - Enlace)
    struct ethhdr *eth = data;
    
    // REGRA DE OURO DO eBPF (Verificador): Você deve provar que não vai ler lixo na memória.
    // "Se o final do cabeçalho ethernet ultrapassar o fim do pacote, aborte."
    /* 
     * ENTENDENDO A MATEMÁTICA: (eth + 1)
     * Como 'eth' é um ponteiro para 'struct ethhdr' (que tem exatos 14 bytes),
     * a linguagem C entende "+ 1" como "pule 1 bloco de 14 bytes para frente".
     * Ou seja, (eth + 1) aponta para o exato primeiro byte APÓS o Ethernet.
     * O (void *) serve apenas para converter para um endereço genérico comparável.
     * 
     * A VERIFICAÇÃO DE LIMITE:
     * O eBPF Verifier nos obriga a perguntar: "Se eu avançar esses 14 bytes, 
     * eu vou acabar caindo fora da memória do pacote (data_end)?"
     * Se sim, o pacote está quebrado ou incompleto. Ignoramos (XDP_PASS).
     */

    if ((void *)(eth + 1) > data_end) 
        return XDP_PASS;

    // Verifica se o pacote é IPv4. 
    // (bpf_ntohs converte a ordem dos bytes da rede para a arquitetura da máquina)
    if (bpf_ntohs(eth->h_proto) != ETH_P_IP) 
        return XDP_PASS;

    // 4. Lendo o Cabeçalho IP (Camada 3 - Rede)
    // O cabeçalho IP começa exatamente onde o cabeçalho Ethernet termina (eth + 1).
    struct iphdr *iph = (void *)(eth + 1);
    
    // Verificador de segurança do eBPF novamente para o cabeçalho IP:
    /* 
     * ENTENDENDO O PASSE DE SEGURANÇA: (iph + 1)
     * A estrutura 'struct iphdr' tem um tamanho estático de 20 bytes no Linux.
     * Sabemos que cabeçalhos IP podem ser maiores que 20 bytes (devido a opções),
     * então (iph + 1) NÃO serve para pular o IP inteiro de forma dinâmica!
     * 
     * Ele serve apenas como um PASSE DE SEGURANÇA. Estamos provando ao Verifier:
     * "Eu garanto que este espaço de memória tem, no mínimo, 20 bytes de tamanho".
     * Apenas após essa prova, o Kernel nos autoriza a ler o campo iph->protocol.
     */
    if ((void *)(iph + 1) > data_end) 
        return XDP_PASS;

    // 5. A Lógica do Firewall
    // Inspeciona qual é o protocolo encapsulado dentro do pacote IP.
    if (iph->protocol == IPPROTO_ICMP) {
        // Se for ICMP (Ping), mata o pacote agora, no driver da placa!
        return XDP_DROP; 
    }
    
    // Se não for ICMP, deixa o pacote continuar sua jornada subindo para o Kernel.
    return XDP_PASS;
}

// Licença obrigatória para o Kernel permitir o carregamento do programa
char _license[] SEC("license") = "GPL";
```

## 2. O Programa TC: Bloqueando Tráfego HTTP (Egress)

Este programa TC atua na saída de rede (Egress). Como ele roda em uma camada mais alta (Traffic Control), o pacote já foi completamente processado pela pilha de rede do Kernel na estrutura __sk_buff. O objetivo aqui é bloquear qualquer tráfego saindo do servidor rumo à porta 80 (HTTP).

```c
#include <linux/bpf.h>
#include <linux/pkt_cls.h> // Define as ações exclusivas do TC (ex: TC_ACT_OK)
#include <linux/if_ether.h>
#include <linux/in.h>
#include <linux/ip.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_endian.h>

// 1. Define onde este programa será anexado (Classificador do Traffic Control)
SEC("classifier")
int tc_egress_filter(struct __sk_buff *skb)
{
    // 2. Extraindo os dados do pacote do contexto sk_buff
    // Mesmo no TC, o eBPF permite o "Direct Packet Access" para ler os dados
    // do pacote com a mesma velocidade que no XDP.
    void *data_end = (void *)(long)skb->data_end;
    void *data     = (void *)(long)skb->data;

    // 3. Lendo o Cabeçalho Ethernet (Camada 2)
    struct ethhdr *eth = data;
    if ((void *)(eth + 1) > data_end) 
        return TC_ACT_OK; // No TC, a ação de aprovação chama-se TC_ACT_OK
        
    if (bpf_ntohs(eth->h_proto) != ETH_P_IP) 
        return TC_ACT_OK;

    // 4. Lendo o Cabeçalho IP (Camada 3)
    struct iphdr *iph = (void *)(eth + 1);
    if ((void *)(iph + 1) > data_end) 
        return TC_ACT_OK;

    // 5. Inspecionando a Camada 4 (Transporte)
    if (iph->protocol == IPPROTO_TCP) {
        
        // Criamos uma estrutura rápida apenas para ler as portas de origem e destino do TCP
        struct tcphdr {
            __be16 source;
            __be16 dest;
        } *tcp;
        
        // Magia dos Ponteiros: O tamanho do cabeçalho IP é variável. 
        // Lemos o tamanho real (iph->ihl * 4 bytes) e somamos ao endereço base do IP 
        // para encontrar o início exato do cabeçalho TCP.
        tcp = (void *)iph + (iph->ihl * 4);

        // Verificador de segurança do eBPF para o cabeçalho TCP:
        if ((void *)(tcp + 1) > data_end) 
            return TC_ACT_OK;

        // 6. A Lógica do Filtro (Bloqueio de HTTP)
        // bpf_htons() garante que o número "80" seja lido no formato correto (Network Byte Order).
        if (tcp->dest == bpf_htons(80)) {
            // Se o destino for a porta 80, bloqueia (atira) no pacote!
            return TC_ACT_SHOT; 
        }
    }
    
    // Libera qualquer outro tráfego (HTTPS, SSH, ICMP, etc)
    return TC_ACT_OK;
}

char _license[] SEC("license") = "GPL";
```
