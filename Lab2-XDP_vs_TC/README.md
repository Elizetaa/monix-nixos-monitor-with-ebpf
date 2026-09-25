# Lab 2: XDP ingress vs. TC egress

Neste laboratório, vamos comparar dois pontos de processamento de pacotes no kernel Linux:

- **XDP (eXpress Data Path):** processa pacotes recebidos muito cedo no caminho de entrada;
- **TC (Traffic Control):** processa pacotes em um ponto posterior da pilha e pode atuar no ingresso ou na saída.

O experimento utiliza XDP para descartar ICMP no ingresso de `h1` e TC para descartar TCP destinado à porta 80 na saída de `h2`.

## Diagrama de Fluxo: XDP vs. TC 

O diagrama abaixo ilustra a jornada de um pacote. Primeiro, observamos o fluxo de entrada (Ingress), subindo do hardware até a aplicação. Em seguida, o fluxo de saída (Egress), descendo da aplicação de volta para o hardware. Observe onde cada programa eBPF é executado em relação à alocação de memória.

### 1. Fluxo de Entrada (INGRESS)

| ⬇️ INGRESS (Subindo para a Aplicação) |
| :--- |
| **🌐 Mídia Física (Cabo / Wi-Fi)**<br>Pacote chega ao servidor |
| ⬇ |
| **⚙️ Driver da Placa de Rede (NIC)** |
| ⬇ |
| 🟢 **Hook: XDP**<br>*Contexto: `xdp_md` (Dados Brutos)*<br>*Ações: PASS, DROP, TX, REDIRECT* |
| ⬇ |
| ⚠️ **Fronteira de Memória**<br>*Kernel aloca a estrutura `sk_buff`* |
| ⬇ |
| 🔵 **Hook: TC (Ingress)**<br>*Contexto: `sk_buff` (Dados + Metadados)*<br>*Ações: OK, SHOT, REDIRECT* |
| ⬇ |
| **🧠 Pilha de Rede do Kernel**<br>Roteamento IP, TCP/UDP, Netfilter |
| ⬇ |
| **🔌 Camada de Sockets**<br>Lê do socket |
| ⬇ |
| **🏢 Aplicação (Userspace)**<br>Recebe os dados processados |

<br>

### 2. Fluxo de Saída (EGRESS)

| ⬇️ EGRESS (Descendo para a Rede) |
| :--- |
| **🏢 Aplicação (Userspace)**<br>Gera os dados e inicia o envio |
| ⬇ |
| **🔌 Camada de Sockets**<br>Escreve no socket |
| ⬇ |
| **🧠 Pilha de Rede do Kernel**<br>Roteamento IP, TCP/UDP, Netfilter |
| ⬇ |
| 🔵 **Hook: TC (Egress)**<br>*Contexto: `sk_buff` (Dados + Metadados)*<br>*Ações: OK, SHOT, REDIRECT* |
| ⬇ |
| ⚠️ **Fronteira de Memória**<br>*Kernel libera a estrutura `sk_buff`* |
| ⬇ |
| ❌ *(Não há hook XDP no fluxo normal de Egress gerado pelo Kernel)* |
| ⬇ |
| **⚙️ Driver da Placa de Rede (NIC)** |
| ⬇ |
| **🌐 Mídia Física (Cabo / Wi-Fi)**<br>Pacote sai do servidor |

> **Observação Importante sobre Egress:** O XDP atua **apenas** no Ingress (recebimento). Embora o XDP possa enviar um pacote de volta pela mesma placa (usando a ação `XDP_TX`) ou redirecionar para outra (usando `XDP_REDIRECT`), um pacote que nasce na sua aplicação (*Userspace*) e desce a pilha de rede passará pelo **TC Egress**, mas **não** passará por um hook XDP ao sair.

### Modo XDP utilizado no laboratório

O Containerlab conecta os hosts com interfaces virtuais do tipo `veth`. Por compatibilidade com essas interfaces no WSL 2, o Makefile anexa o programa com `xdpgeneric`, isto é, no modo XDP genérico.

O programa, o hook e as ações XDP continuam sendo estudados normalmente. Entretanto, este experimento demonstra o comportamento do filtro, e não o desempenho do XDP nativo executado pelo driver de uma interface física.

## Objetivos

Ao final, você deverá conseguir:

- diferenciar ingress e egress;
- explicar onde XDP e TC atuam;
- carregar programas eBPF em interfaces de namespaces de rede;
- comparar o comportamento antes e depois de cada filtro;
- reconhecer por que um teste de linha de base evita falsos positivos.

## Topologia

O Containerlab cria dois hosts Linux conectados diretamente:

```text
h1 (10.10.12.1/24) eth1 ─── eth1 h2 (10.10.12.2/24)
```

- `h1` recebe o programa XDP em `eth1` e executa um servidor TCP na porta 80;
- `h2` gera ping e conexões TCP e recebe o filtro TC egress em `eth1`.

A rede experimental `10.10.12.0/24` é diferente da rede administrativa do Containerlab. Essa separação garante que os pacotes de teste atravessem `eth1`, em vez de seguirem pela interface administrativa `eth0` e desviarem dos programas eBPF.

## Arquivos

- `topology.yml`: descreve os containers e o enlace;
- `xdp_drop_icmp.bpf.c`: retorna `XDP_DROP` para pacotes IPv4/ICMP;
- `tc_egress_drop.bpf.c`: retorna `TC_ACT_SHOT` para TCP com destino à porta 80;
- `Makefile`: verifica, compila, executa, testa e limpa o laboratório.

[Consulte uma explicação mais detalhada sobre os códigos eBPF aqui](CODES.md)

## Ferramentas de visualização

O laboratório utiliza ferramentas de terminal instaladas automaticamente nos containers:

- `ping`: mostra cada resposta ICMP e o percentual de perda;
- `tcpdump`: exibe os pacotes que realmente atravessam `eth1`;
- `ip -details link`: mostra o programa XDP anexado à interface;
- `nc -v`: mostra se a conexão TCP foi estabelecida ou expirou;
- `tc -s`: mostra o classificador TC e seus contadores.

Essa combinação permite observar o experimento diretamente no terminal e é mais leve que uma interface gráfica para as máquinas do curso.

## Antes de começar

Este passo a passo considera que o ambiente do README principal já foi preparado e que você está na raiz de `ebpf-grad-labs`.

Confirme:

```bash
pwd
ls
```

### NixOS

O `shell.nix` deste diretório fornece Clang, GCC, Make, libbpf e os headers
Linux usados na compilação. Entre no ambiente antes de compilar:

```bash
cd Lab2-XDP_vs_TC
nix-shell
```

No NixOS, habilite Docker e instale o Containerlab e as ferramentas de rede
na configuração do sistema:

```nix
virtualisation.docker.enable = true;
environment.systemPackages = with pkgs; [ containerlab iproute2 ];
```

Reaplique a configuração e inicie o daemon:

```bash
sudo nixos-rebuild switch
sudo systemctl start docker
```

Depois, continue com `make check` e `make setup`. O Makefile detecta a
arquitetura do host para gerar bytecode BPF compatível.

## Executar

### 1. Entre no Lab 2

```bash
cd Lab2-XDP_vs_TC
```

### 2. Conheça os alvos

```bash
make help
```

### 3. Verifique o ambiente

```bash
make check
```

Essa etapa confirma o ambiente possui Clang, Docker, Containerlab, `ip`, `tc` e o Docker daemon.

### 4. Prepare o ambiente

```bash
make setup
```
O Makefile compila os programas, cria os dois containers e configura a rede experimental. Nenhum filtro é anexado e nenhum teste é executado nessa etapa.

Depois de `make setup`, a topologia permanece ativa durante os experimentos e só é removida quando você executa `make clean`.

## Experimentos

O Lab 2 possui dois experimentos separados. Primeiro estudamos XDP no ingresso de `h1`; depois estudamos TC na saída de `h2`.

```bash
# Executa a linha de base do XDP ainda sem o filtro.
make test-xdp-baseline

# Anexa o XDP e testa o bloqueio de ICMP no ingresso.
make test-xdp-filter

# Remove o programa XDP antes do experimento seguinte.
make detach-xdp

# Executa a linha de base do TC ainda sem o filtro.
make test-tc-baseline

# Anexa o TC e testa o bloqueio de TCP/80 no egress.
make test-tc-filter

# Remove o classificador TC ao final do experimento.
make detach-tc
```


## Experimento 1: XDP no ingresso

### O que estamos estudando

XDP permite executar um programa eBPF quando um pacote entra em uma interface de rede. Ele atua antes de grande parte do processamento tradicional da pilha Linux.

Neste laboratório, o programa `xdp_drop_icmp.bpf.c` é anexado ao ingresso de `eth1` em `h1`:

```text
h2 envia ICMP
      |
      v
h1:eth1 -- entrada --> XDP --> pilha de rede de h1
                         |
                         +-- XDP_DROP: descarta ICMP
                         +-- XDP_PASS: permite os demais pacotes
```

O programa verifica o protocolo do pacote. Quando encontra ICMP, utilizado pelo `ping`, retorna `XDP_DROP`. Para os demais protocolos, retorna `XDP_PASS`.

Usaremos dois testes: uma linha de base sem XDP e a repetição do mesmo ping com XDP. Comparar os dois resultados permite atribuir o bloqueio ao programa eBPF.

### XDP — teste 1/2: linha de base sem filtro

Execute:

```bash
# Testa a comunicação ICMP antes de anexar o programa XDP.
make test-xdp-baseline
```

Antes de anexar XDP, `h2` envia ICMP para `h1`:

```text
h2 ── ping ──> h1
```

O Makefile inicia `tcpdump` na `eth1` de `h1` e envia dois pings. As saídas técnicas de `tcpdump` e `ping` são exibidas durante a execução e, quando os comandos terminam, o Makefile apresenta um resumo didático que interpreta os resultados observados.

Essa ordem é intencional: alunos curiosos podem examinar toda a evidência técnica, enquanto a tela final oferece uma explicação acessível sem se misturar aos dados brutos.

Resultado esperado:

```text
============================================================
 XDP - TESTE 1/2: comunicacao antes de ativar o filtro
============================================================

OBJETIVO
  Verificar se h2 consegue conversar com h1 sem nenhum
  bloqueio eBPF. Este resultado sera nossa linha de base.

PREPARACAO
  [OK] XDP esta desativado em h1.
  [OK] tcpdump observou a interface eth1.

ACAO
  h2 enviou 2 pedidos de ping para h1 (10.10.12.1).
  ICMP e o protocolo utilizado pelo comando ping.

O QUE ACONTECEU
1. h2 enviou 2 pedidos de ping para h1.
2. h1 recebeu os 2 pedidos pela interface eth1.
3. h1 enviou 2 respostas para h2.
4. h2 recebeu as 2 respostas: nenhuma foi perdida.

PACOTES OBSERVADOS PELO TCPDUMP
  Pedidos:   2  |  h2 (10.10.12.2) -> h1 (10.10.12.1)
  Respostas: 2  |  h1 (10.10.12.1) -> h2 (10.10.12.2)

CONCLUSAO
  [SUCESSO] A rede funciona normalmente sem o XDP.
```

Essa é a linha de base: ela prova que endereçamento, enlace e rota funcionam antes do filtro.

### XDP — teste 2/2: filtro no ingresso

Execute:

```bash
# Anexa o XDP ao ingresso de h1 e testa o descarte de pacotes ICMP.
make test-xdp-filter
```

O programa é anexado a `eth1` de `h1`. Pacotes ICMP recebidos passam pelo hook XDP e retornam `XDP_DROP`.

O comando `ip -details link` mostra `prog/xdp` na interface. Em seguida, a saída real do ping deve mostrar que nenhuma resposta chegou:

```text
2 packets transmitted, 0 received, 100% packet loss
```

Resultado esperado:

```text
[SUCESSO] O XDP bloqueou ICMP no ingresso de h1.
```

O ping falhar agora é uma evidência válida porque a mesma comunicação funcionou no teste XDP anterior.

### Encerre o experimento XDP

Depois de observar o bloqueio, desanexe o programa antes de iniciar o experimento TC:

```bash
# Remove somente o programa XDP anexado a h1:eth1.
make detach-xdp
```

Esse comando remove somente o XDP de `h1:eth1`. Os containers, os endereços e a topologia permanecem ativos para o experimento seguinte.

## Experimento 2: TC na saída

### O que estamos estudando

TC, ou Traffic Control, oferece hooks eBPF associados ao sistema de controle de tráfego do Linux. Diferentemente do experimento XDP, usaremos o hook de **egress**, que observa os pacotes quando eles estão saindo de `h2`.

O programa `tc_egress_drop.bpf.c` é anexado ao egress de `eth1` em `h2`:

```text
aplicacao em h2 tenta conectar na porta 80
                    |
                    v
pilha de rede de h2 --> TC egress --> h2:eth1 --> h1
                           |
                           +-- TC_ACT_SHOT: descarta TCP/80
                           +-- TC_ACT_OK: permite os demais pacotes
```

Agora o tráfego testado não é ICMP, mas uma conexão TCP destinada à porta 80. O Netcat cria o servidor e tenta estabelecer a conexão; `tcpdump` e os contadores do TC fornecem as evidências técnicas.

### TC — teste 1/2: linha de base sem filtro

Execute:

```bash
# Testa a conexão TCP/80 antes de anexar o classificador TC.
make test-tc-baseline
```

O Makefile inicia um servidor com Netcat na porta 80 de `h1`. Antes do TC, `h2` precisa conseguir abrir uma conexão TCP.

O `tcpdump` mostra a negociação na `eth1` de `h1`, enquanto `nc -v` apresenta:

```text
Connection to 10.10.12.1 80 port [tcp/http] succeeded!
```

Resultado esperado:

```text
[SUCESSO] TCP/80 funciona normalmente sem o filtro TC.
```

Essa linha de base prova que o servidor, a porta e o caminho de rede funcionam antes do filtro TC.

### TC — teste 2/2: filtro no egress

Execute:

```bash
# Anexa o TC ao egress de h2 e testa o descarte de tráfego TCP/80.
make test-tc-filter
```

O classificador TC é anexado ao egress de `eth1` em `h2`. Quando o destino TCP é a porta 80, o programa retorna `TC_ACT_SHOT`.

O Makefile mostra primeiro o filtro anexado. A tentativa de conexão deve expirar e, depois, `tc -s` exibe os contadores do classificador que processou o pacote.

Resultado esperado:

```text
[SUCESSO] O TC bloqueou TCP/80 no egress de h2.
```

### Encerre o experimento TC

Depois de observar os contadores e o bloqueio, remova o classificador TC:

```bash
make detach-tc
```

Esse comando remove o `clsact` e o programa eBPF anexado ao egress de `h2:eth1`, mas preserva a topologia até que `make clean` seja executado.

## Comparando XDP e TC

Os dois experimentos descartam pacotes com programas eBPF, mas fazem isso em locais e momentos diferentes:

| Característica | XDP neste laboratório | TC neste laboratório |
|---|---|---|
| Direção observada | Ingresso | Egress |
| Interface | `h1:eth1` | `h2:eth1` |
| Momento | Muito cedo na entrada | Na saída, depois da pilha de rede |
| Tráfego testado | ICMP do `ping` | TCP destinado à porta 80 |
| Ação de descarte | `XDP_DROP` | `TC_ACT_SHOT` |
| Ferramenta principal de evidência | `ping`, `tcpdump` e `ip -details` | Netcat, `tcpdump` e `tc -s` |

O objetivo não é afirmar que uma tecnologia é sempre melhor que a outra. A escolha depende do ponto do caminho de rede em que o programa precisa atuar e das informações necessárias para tomar a decisão.

Neste exemplo:

- XDP demonstra uma decisão muito antecipada sobre pacotes recebidos por `h1`;
- TC demonstra uma política aplicada aos pacotes que tentam sair de `h2`;
- as linhas de base mostram que ambos os bloqueios foram causados pelos programas eBPF, e não por uma falha prévia da rede.

## Evidências do experimento

A topologia continua ativa depois de cada teste. Em outro terminal, você também pode inspecionar:

```bash
# Entra no namespace de h1 e mostra detalhes do XDP anexado a eth1.
sudo ip netns exec h1 ip -details link show dev eth1
```

```bash
# Entra no namespace de h2 e lista os filtros TC no egress de eth1.
sudo ip netns exec h2 tc filter show dev eth1 egress
```

```bash
# Lista todos os programas eBPF atualmente carregados no kernel.
sudo bpftool prog list
```

O teste é bem-sucedido quando:

1. ICMP funciona antes e falha depois do XDP;
2. TCP/80 funciona antes e falha depois do TC;
3. os programas aparecem anexados nos pontos esperados.

## Encerrar e limpar

Quando terminar os testes e a inspeção, destrua a topologia, remova os namespaces auxiliares e apague `xdp.o` e `tc.o`:

```bash
# Destrói a topologia e remove os namespaces e objetos compilados do Lab 2.
make clean
```

Confirme:

```bash
# Procura containers do Lab 2 ainda ativos; exibe a mensagem se não encontrar nenhum.
sudo docker ps --format '{{.Names}}' | grep clab-ebpf-lab2 || echo "Lab 2 removido"
```

## Solução de problemas

### Docker daemon indisponível

```bash
# Inicia o serviço do Docker dentro do Ubuntu/WSL.
sudo service docker start

# Repete a verificação do ambiente depois de iniciar o serviço.
make check
```

No NixOS, use `sudo systemctl start docker`.

### Uma topologia anterior ainda está ativa

```bash
# Remove uma execução anterior e seus recursos.
make clean

# Recria a topologia e recompila os programas.
make setup
```

### Falha durante a instalação de pacotes nos containers

Confirme o acesso à internet e tente novamente. Na primeira execução, os containers instalam `iproute2`, `iputils-ping`, `netcat-openbsd` e `tcpdump`.

### Falha ao anexar o programa XDP

Primeiro, remova qualquer programa XDP que tenha permanecido anexado e repita o teste:

```bash
# Remove qualquer XDP que tenha permanecido anexado.
make detach-xdp

# Repete o teste que anexa o filtro XDP.
make test-xdp-filter
```

Se a falha continuar, confira se o kernel oferece o tipo de programa XDP:

```bash
# Examina os recursos e tipos XDP oferecidos pelo kernel.
sudo bpftool feature probe kernel | grep -i -A 8 xdp
```

Confira também o estado da interface virtual:

```bash
# Exibe detalhes da interface eth1 dentro do namespace de h1.
sudo ip netns exec h1 ip -details link show dev eth1
```

Se o kernel WSL estiver desatualizado, execute no PowerShell do Windows:

```powershell
# Baixa e instala a atualização mais recente disponível para o WSL.
wsl --update

# Encerra as distribuições e o kernel do WSL para aplicar a atualização.
wsl --shutdown
```

Abra novamente o Ubuntu, inicie o Docker e recrie o laboratório:

```bash
# Inicia o serviço do Docker na nova sessão Ubuntu.
sudo service docker start

# Entra diretamente no diretório do Lab 2.
cd ~/ebpf-grad-labs/Lab2-XDP_vs_TC

# Remove recursos que possam ter restado da execução anterior.
make clean

# Compila os programas e recria a topologia.
make setup

# Confirma que a comunicação ICMP funciona sem o filtro.
make test-xdp-baseline

# Anexa o XDP e repete o teste de bloqueio.
make test-xdp-filter
```
