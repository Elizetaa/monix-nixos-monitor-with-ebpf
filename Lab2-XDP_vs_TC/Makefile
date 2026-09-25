# Usa Bash porque os alvos setup e test utilizam funcoes, trap e pipefail.
SHELL := bash

# Flags de headers podem ser fornecidas pelo ambiente Nix ou pelo pkg-config.
ARCH_INCLUDE ?= $(shell gcc -print-multiarch 2>/dev/null)
BPF_ARCH := $(shell uname -m | sed -e 's/x86_64/x86/' -e 's/aarch64/arm64/' -e 's/ppc64le/powerpc/' -e 's/s390x/s390/' -e 's/riscv64/riscv/')
BPF_CFLAGS := $(NIX_CFLAGS_COMPILE) $(shell pkg-config --cflags libbpf 2>/dev/null)

# Nomes criados pelo Containerlab a partir de topology.yml.
LAB_NAME := ebpf-lab2
H1_CONTAINER := clab-$(LAB_NAME)-h1
H2_CONTAINER := clab-$(LAB_NAME)-h2

# Rede exclusiva do enlace experimental h1--h2. Ela deve ser diferente da
# rede administrativa criada pelo Containerlab (normalmente 172.20.20.0/24),
# para que os testes atravessem eth1 e alcancem os hooks XDP e TC.
H1_LAB_IP := 10.10.12.1
H2_LAB_IP := 10.10.12.2
LAB_PREFIX := 24

.PHONY: help check build setup verify-env \
	test-xdp-baseline test-xdp-filter test-tc-baseline test-tc-filter \
	detach-xdp detach-tc destroy clean

# Mostra a interface publica do laboratorio.
help:
# Identifica o laboratorio antes de apresentar os alvos disponiveis.
	@echo "Lab 2 - XDP vs. TC"
# Documenta os comandos que o aluno pode executar sem modificar o ambiente.
	@echo "  make check    verifica as dependencias"
	@echo "  make build    compila os programas eBPF"
	@echo "  make setup    compila e monta a topologia"
	@echo "  make test-xdp-baseline  mostra o ping funcionando sem XDP"
	@echo "  make test-xdp-filter    anexa XDP e mostra o bloqueio do ping"
	@echo "  make detach-xdp         desanexa o programa XDP"
	@echo "  make test-tc-baseline   mostra TCP/80 funcionando sem TC"
	@echo "  make test-tc-filter     anexa TC e mostra o bloqueio de TCP/80"
	@echo "  make detach-tc          desanexa o programa TC"
	@echo "  make destroy  destroi uma topologia remanescente"
	@echo "  make clean    destroi a topologia e remove objetos"

# Verifica ferramentas do host antes de criar containers ou alterar a rede.
check:
# Confirma o Clang, que compila os programas XDP e TC para bytecode eBPF.
	@command -v clang >/dev/null || { \
		echo "ERRO: Clang nao foi encontrado; sem ele XDP e TC nao podem ser compilados."; \
		echo "Instale com: sudo apt update && sudo apt install -y clang llvm"; \
		echo "Depois, execute novamente: make check"; exit 1; }
# Confirma o GCC, usado para localizar os headers especificos da arquitetura.
	@command -v gcc >/dev/null || { \
		echo "ERRO: GCC nao foi encontrado; ele e usado para localizar os headers da arquitetura."; \
		echo "Instale GCC e as ferramentas de compilacao pelo gerenciador de pacotes da sua distribuicao."; \
		echo "Depois, execute novamente: make check"; exit 1; }
# Confirma o cliente Docker, pois o Containerlab cria h1 e h2 como containers.
	@command -v docker >/dev/null || { \
		echo "ERRO: Docker nao foi encontrado; o Containerlab depende dele para criar os hosts."; \
		echo "Volte ao README principal e conclua a secao 'Instalar e testar o Docker'."; \
		echo "Instalacao usada pelo curso:"; \
		echo "  curl -fsSL https://get.docker.com -o get-docker.sh"; \
		echo "  sudo sh get-docker.sh"; exit 1; }
# Confirma o Containerlab, responsavel por interpretar topology.yml e criar o
# enlace virtual do experimento.
	@command -v containerlab >/dev/null || { \
		echo "ERRO: Containerlab nao foi encontrado; a topologia h1--h2 nao pode ser criada."; \
		echo "Volte ao README principal e conclua a secao 'Instalar e testar o Containerlab'."; \
		echo "Instale com: bash -c \"\$$(curl -sL https://get.containerlab.dev)\""; \
		echo "Depois, confirme com: containerlab version"; exit 1; }
# Confirma o comando ip, necessario para configurar namespaces, enderecos e XDP.
	@command -v ip >/dev/null || { \
		echo "ERRO: o comando ip nao foi encontrado; os namespaces nao podem ser configurados."; \
		echo "Instale com: sudo apt install -y iproute2"; exit 1; }
# Confirma o comando tc, usado para criar o qdisc clsact e anexar o classificador
# eBPF ao egress de h2.
	@command -v tc >/dev/null || { \
		echo "ERRO: o comando tc nao foi encontrado; o filtro TC egress nao pode ser anexado."; \
		echo "Instale com: sudo apt install -y iproute2"; exit 1; }
# Consulta o daemon Docker de verdade, pois a presenca do cliente nao garante
# que o servico capaz de criar containers esteja ativo.
	@sudo docker info >/dev/null 2>&1 || { \
		echo "ERRO: Docker esta instalado, mas o daemon nao esta em execucao ou nao respondeu."; \
		echo "Inicie o servico Docker pelo gerenciador de servicos da sua distribuicao (no NixOS: sudo systemctl start docker)."; \
		echo "Teste com: sudo docker info"; \
		echo "Se continuar falhando, consulte 'Docker daemon nao esta rodando' no README principal."; exit 1; }
# Informa sucesso somente depois que todas as verificacoes passaram.
	@echo "Dependencias basicas do Lab 2 encontradas."

# Compila um programa XDP e um classificador TC para bytecode eBPF.
# -O2 facilita a verificacao pelo verifier e -target bpf seleciona o backend BPF.
build: check
# Separa visualmente a compilacao das verificacoes realizadas por check.
	@echo "Compilando os programas XDP e TC..."
# Compila o filtro de ICMP para xdp.o, usando otimizacao adequada ao verifier e
# os headers da arquitetura atual.
	@clang -O2 -g -target bpf -D__TARGET_ARCH_$(BPF_ARCH) $(BPF_CFLAGS) $(if $(ARCH_INCLUDE),-I"$(ARCH_INCLUDE)") \
		-c xdp_drop_icmp.bpf.c -o xdp.o || { \
			echo "ERRO: falha ao compilar o programa XDP."; \
			echo "Leia o diagnostico do Clang acima e confirme clang, libbpf-dev e os headers."; exit 1; }
# Compila o classificador de TCP/80 para tc.o com os mesmos requisitos eBPF.
	@clang -O2 -g -target bpf -D__TARGET_ARCH_$(BPF_ARCH) $(BPF_CFLAGS) $(if $(ARCH_INCLUDE),-I"$(ARCH_INCLUDE)") \
		-c tc_egress_drop.bpf.c -o tc.o || { \
			echo "ERRO: falha ao compilar o programa TC."; \
			echo "Leia o diagnostico do Clang acima e confirme clang, libbpf-dev e os headers."; exit 1; }
# Confirma que os dois objetos foram produzidos sem esconder erros do Clang.
	@echo "Programas eBPF compilados."

# Monta a topologia e configura apenas o ambiente. Nenhum teste e executado e
# nenhum programa eBPF e anexado nesta etapa.
setup: build
# Executa a preparacao em um unico shell para compartilhar funcoes, traps e
# variaveis. Antes do deploy, verifica h1 e h2 e preserva qualquer topologia
# anterior, orientando make clean em vez de remove-la automaticamente.
#
# Depois usa --reconfigure para descartar apenas artefatos de uma implantacao
# antiga que nao possui mais containers. Em seguida cria a topologia, obtem os
# PIDs, expoe os namespaces em /var/run/netns e configura 10.10.12.0/24 em
# eth1. Se uma etapa posterior ao deploy falhar, cleanup remove a implantacao
# incompleta.
	@set -Eeuo pipefail; \
	existing_containers=""; \
	for container in "$(H1_CONTAINER)" "$(H2_CONTAINER)"; do \
		if sudo docker inspect "$$container" >/dev/null 2>&1; then \
			existing_containers="$$existing_containers $$container"; \
		fi; \
	done; \
	if [ -n "$$existing_containers" ]; then \
		echo "AVISO: ja existem containers de uma topologia anterior do Lab 2:"; \
		for container in $$existing_containers; do echo "  $$container"; done; \
		echo "Se nao quiser manter esse laboratorio, execute primeiro: make clean"; \
		exit 1; \
	fi; \
	cleanup() { \
		sudo containerlab destroy -t topology.yml --cleanup >/dev/null 2>&1 || true; \
		sudo rm -f /var/run/netns/h1 /var/run/netns/h2; \
	}; \
	on_error() { \
		status=$$?; \
		echo "ERRO: nao foi possivel preparar a topologia do Lab 2."; \
		echo "Leia a mensagem imediatamente acima: ela indica a etapa ou comando que falhou."; \
		echo "A preparacao incompleta sera removida automaticamente."; \
		cleanup; \
		exit $$status; \
	}; \
	trap cleanup INT TERM; \
	trap on_error ERR; \
	sudo containerlab deploy -t topology.yml --reconfigure; \
	H1_PID=$$(sudo docker inspect -f '{{.State.Pid}}' "$(H1_CONTAINER)"); \
	H2_PID=$$(sudo docker inspect -f '{{.State.Pid}}' "$(H2_CONTAINER)"); \
	sudo mkdir -p /var/run/netns; \
	sudo ln -sf "/proc/$$H1_PID/ns/net" /var/run/netns/h1; \
	sudo ln -sf "/proc/$$H2_PID/ns/net" /var/run/netns/h2; \
	sudo ip netns exec h1 ip addr replace "$(H1_LAB_IP)/$(LAB_PREFIX)" dev eth1; \
	sudo ip netns exec h2 ip addr replace "$(H2_LAB_IP)/$(LAB_PREFIX)" dev eth1; \
	sudo ip netns exec h1 ip link set eth1 up; \
	sudo ip netns exec h2 ip link set eth1 up; \
	echo "Ambiente do Lab 2 preparado."; \
	echo "Topologia ativa: h1 ($(H1_LAB_IP)) <-> h2 ($(H2_LAB_IP))"; \
	echo "Inicie o experimento XDP com: make test-xdp-baseline"

# Verificacao compartilhada pelos quatro testes. Em vez de apenas examinar os
# links de /var/run/netns, executa um comando real dentro de cada namespace.
verify-env:
# Verifica os dois objetos locais antes de permitir qualquer teste.
	@test -f xdp.o -a -f tc.o || { \
		echo "ERRO: os programas eBPF do Lab 2 nao foram compilados."; \
		echo "Execute primeiro: make setup"; exit 1; }
# Executa um comando neutro dentro dos namespaces para confirmar que os links
# apontam para containers ativos, e nao apenas para caminhos antigos.
	@sudo ip netns exec h1 true >/dev/null 2>&1 && \
	 sudo ip netns exec h2 true >/dev/null 2>&1 || { \
		echo "ERRO: os namespaces h1 e h2 nao estao acessiveis."; \
		echo "A topologia pode ter sido encerrada. Execute novamente: make setup"; exit 1; }

# Etapa 1: prova visualmente que a rede funciona antes do XDP. tcpdump observa
# na eth1 de h1 a requisicao e a resposta ICMP mostradas tambem pelo ping.
test-xdp-baseline: verify-env
# Cria arquivos temporarios para preservar as saidas completas de ping e
# tcpdump, remove qualquer XDP anterior e captura os quatro pacotes esperados.
# A contagem de pedidos e respostas comprova que a rede funciona antes do
# filtro; os arquivos temporarios sao removidos automaticamente ao sair.
	@set -euo pipefail; \
	capture_file=$$(mktemp); \
	ping_file=$$(mktemp); \
	cleanup_logs() { rm -f "$$capture_file" "$$ping_file"; }; \
	trap cleanup_logs EXIT; \
	sudo ip netns exec h1 ip link set dev eth1 xdpgeneric off >/dev/null 2>&1 || true; \
	sudo ip netns exec h1 timeout 6 tcpdump -n -l -i eth1 -c 4 icmp 2>&1 | tee "$$capture_file" & capture_pid=$$!; \
	sleep 1; \
	if ! sudo ip netns exec h2 ping -c 2 -W 2 "$(H1_LAB_IP)" 2>&1 | tee "$$ping_file"; then \
		wait $$capture_pid || true; \
		echo; \
		echo "ERRO: h2 nao conseguiu pingar h1 antes do XDP."; \
		echo "A linha de base falhou; verifique a topologia e os IPs."; \
		echo "Saida tecnica do ping:"; \
		cat "$$ping_file"; \
		exit 1; \
	fi; \
	wait $$capture_pid || true; \
	request_count=$$(grep -c 'echo request' "$$capture_file" || true); \
	reply_count=$$(grep -c 'echo reply' "$$capture_file" || true); \
	if [ "$$request_count" -lt 2 ] || [ "$$reply_count" -lt 2 ]; then \
		echo "ERRO: o ping respondeu, mas o tcpdump nao observou os 4 pacotes esperados."; \
		echo "Saida tecnica da captura:"; \
		cat "$$capture_file"; \
		exit 1; \
	fi; \
	echo; \
	echo "============================================================"; \
	echo " XDP - TESTE 1/2: comunicacao antes de ativar o filtro"; \
	echo "============================================================"; \
	echo; \
	echo "OBJETIVO"; \
	echo "  Verificar se h2 consegue conversar com h1 sem nenhum"; \
	echo "  bloqueio eBPF. Este resultado sera nossa linha de base."; \
	echo; \
	echo "PREPARACAO"; \
	echo "  [OK] XDP esta desativado em h1."; \
	echo "  [OK] tcpdump observou a interface eth1."; \
	echo; \
	echo "ACAO"; \
	echo "  h2 enviou 2 pedidos de ping para h1 ($(H1_LAB_IP))."; \
	echo "  ICMP e o protocolo utilizado pelo comando ping."; \
	echo; \
	echo "O QUE ACONTECEU"; \
	echo "1. h2 enviou 2 pedidos de ping para h1."; \
	echo "2. h1 recebeu os 2 pedidos pela interface eth1."; \
	echo "3. h1 enviou 2 respostas para h2."; \
	echo "4. h2 recebeu as 2 respostas: nenhuma foi perdida."; \
	echo; \
	echo "PACOTES OBSERVADOS PELO TCPDUMP"; \
	echo "  Pedidos:   $$request_count  |  h2 ($(H2_LAB_IP)) -> h1 ($(H1_LAB_IP))"; \
	echo "  Respostas: $$reply_count  |  h1 ($(H1_LAB_IP)) -> h2 ($(H2_LAB_IP))"; \
	echo; \
	echo "CONCLUSAO"; \
	echo "  [SUCESSO] A rede funciona normalmente sem o XDP."; \
	echo "  Na proxima etapa, repetiremos o ping com o XDP ativo."; \
	echo "  Se as respostas desaparecerem, saberemos que o XDP causou o bloqueio."; \
	echo

# Etapa 2: anexa XDP generico ao ingresso de h1, exibe os detalhes do link e
# deixa o ping mostrar explicitamente a perda causada por XDP_DROP.
test-xdp-filter: verify-env
# Garante uma anexacao nova em modo generico, mostra o programa na interface e
# repete o ping. Agora a falha do ping e o resultado esperado: se ele responder,
# o alvo interrompe porque XDP_DROP nao produziu o bloqueio estudado.
	@set -euo pipefail; \
	sudo ip netns exec h1 ip link set dev eth1 xdpgeneric off >/dev/null 2>&1 || true; \
	if ! sudo ip netns exec h1 ip link set dev eth1 xdpgeneric obj "$(CURDIR)/xdp.o" sec xdp; then \
		echo "ERRO: nao foi possivel anexar o programa XDP generico a eth1 de h1."; \
		echo "O modo generico e usado porque pares veth no WSL podem rejeitar XDP nativo por causa do MTU do peer."; \
		echo "Verifique o suporte do kernel com: sudo bpftool feature probe kernel"; \
		exit 1; \
	fi; \
	echo; \
	sudo ip netns exec h1 ip -details link show dev eth1; \
	echo; \
	if sudo ip netns exec h2 ping -c 2 -W 2 "$(H1_LAB_IP)"; then \
		echo "FALHA: ping passou mesmo com XDP."; \
		exit 1; \
	else \
		true; \
	fi; \
	echo; \
	echo "============================================================"; \
	echo " XDP - TESTE 2/2: bloquear o ping no ingresso de h1"; \
	echo "============================================================"; \
	echo; \
	echo "OBJETIVO"; \
	echo "  Anexar o programa XDP a eth1 de h1 e comprovar que"; \
	echo "  XDP_DROP impede a chegada dos pedidos de ping."; \
	echo; \
	echo "PREPARACAO"; \
	echo "  [OK] XDP generico foi anexado ao ingresso de h1:eth1."; \
	echo; \
	echo "ACAO"; \
	echo "  h2 enviou 2 pedidos de ping para h1 com o XDP ativo."; \
	echo; \
	echo "O QUE ACONTECEU"; \
	echo "1. O programa XDP foi anexado ao ingresso de h1:eth1."; \
	echo "2. h2 enviou 2 pedidos de ping para h1."; \
	echo "3. XDP_DROP descartou os pedidos antes da pilha de rede."; \
	echo "4. h2 nao recebeu respostas e mostrou 100% de perda."; \
	echo; \
	echo "CONCLUSAO"; \
	echo "  [SUCESSO] O XDP bloqueou ICMP no ingresso de h1."; \
	echo "  Compare este resultado com a comunicacao normal da etapa 1."; \
	echo

# Etapa 3: inicia o servidor e captura a negociacao TCP em h1. A saida verbose
# do Netcat e os pacotes do tcpdump formam a linha de base antes do TC.
test-tc-baseline: verify-env
# Remove filtros XDP e TC que poderiam contaminar a linha de base, inicia o
# servidor Netcat em h1 e captura a negociacao TCP/80. A conexao precisa
# funcionar para atribuir ao TC qualquer bloqueio observado na etapa seguinte.
	@set -euo pipefail; \
	capture_file=$$(mktemp); \
	nc_file=$$(mktemp); \
	cleanup_logs() { rm -f "$$capture_file" "$$nc_file"; }; \
	trap cleanup_logs EXIT; \
	sudo ip netns exec h1 ip link set dev eth1 xdpgeneric off >/dev/null 2>&1 || true; \
	sudo ip netns exec h2 tc qdisc del dev eth1 clsact >/dev/null 2>&1 || true; \
	sudo ip netns exec h1 sh -c 'nohup nc -lk -p 80 >/tmp/lab2-nc.log 2>&1 &'; \
	sleep 1; \
	sudo ip netns exec h1 timeout 6 tcpdump -n -l -i eth1 -c 4 'tcp port 80' 2>&1 | tee "$$capture_file" & capture_pid=$$!; \
	sleep 1; \
	if ! sudo ip netns exec h2 nc -v -z -w 2 "$(H1_LAB_IP)" 80 2>&1 | tee "$$nc_file"; then \
		wait $$capture_pid || true; \
		echo "ERRO: a conexao TCP/80 falhou antes do filtro TC."; \
		echo "Verifique se netcat-openbsd foi instalado no container h1 e se o servidor nc iniciou."; \
		exit 1; \
	fi; \
	wait $$capture_pid || true; \
	echo; \
	echo "============================================================"; \
	echo " TC - TESTE 1/2: conexao TCP/80 antes de ativar o filtro"; \
	echo "============================================================"; \
	echo; \
	echo "OBJETIVO"; \
	echo "  Confirmar que h2 consegue acessar o servidor de h1 na"; \
	echo "  porta TCP 80 antes de instalar o filtro TC."; \
	echo; \
	echo "PREPARACAO"; \
	echo "  [OK] TC esta desativado em h2."; \
	echo "  [OK] O servidor esta escutando a porta 80 em h1."; \
	echo; \
	echo "ACAO"; \
	echo "  h2 abriu uma conexao TCP/80 enquanto tcpdump observava."; \
	echo; \
	echo "O QUE ACONTECEU"; \
	echo "1. h1 manteve um servidor escutando na porta TCP 80."; \
	echo "2. h2 iniciou uma conexao com esse servidor."; \
	echo "3. O Netcat confirmou a conexao e tcpdump viu os pacotes."; \
	echo; \
	echo "CONCLUSAO"; \
	echo "  [SUCESSO] TCP/80 funciona normalmente sem o filtro TC."; \
	echo "  Esta e a linha de base para a proxima etapa."; \
	echo

# Etapa 4: anexa o classificador ao egress de h2. A conexao deve expirar e
# tc -s mostra o programa e os contadores de pacotes processados pelo hook.
test-tc-filter: verify-env
# Mantem o servidor ativo, cria o qdisc clsact e anexa tc.o ao egress de h2.
# A tentativa TCP/80 deve falhar; em seguida, tc -s mostra o classificador e
# seus contadores como evidencia de que o pacote atravessou o hook.
	@set -euo pipefail; \
	sudo ip netns exec h1 sh -c 'nohup nc -lk -p 80 >/tmp/lab2-nc.log 2>&1 &' || true; \
	sudo ip netns exec h2 tc qdisc replace dev eth1 clsact; \
	sudo ip netns exec h2 tc filter replace dev eth1 egress bpf da obj "$(CURDIR)/tc.o" sec classifier; \
	sudo ip netns exec h2 tc filter show dev eth1 egress; \
	echo; \
	if sudo ip netns exec h2 nc -v -z -w 2 "$(H1_LAB_IP)" 80; then \
		echo "FALHA: conexao TCP/80 passou mesmo com TC."; \
		exit 1; \
	else \
		true; \
	fi; \
	echo; \
	sudo ip netns exec h2 tc -s filter show dev eth1 egress; \
	echo; \
	echo "============================================================"; \
	echo " TC - TESTE 2/2: bloquear TCP/80 na saida de h2"; \
	echo "============================================================"; \
	echo; \
	echo "OBJETIVO"; \
	echo "  Anexar o programa TC ao egress de h2:eth1 e impedir"; \
	echo "  que a tentativa de conexao TCP/80 saia de h2."; \
	echo; \
	echo "PREPARACAO"; \
	echo "  [OK] O servidor TCP/80 esta ativo em h1."; \
	echo "  [OK] O programa TC esta anexado ao egress de h2:eth1."; \
	echo; \
	echo "ACAO"; \
	echo "  h2 tentou abrir uma conexao TCP/80 com h1."; \
	echo; \
	echo "O QUE ACONTECEU"; \
	echo "1. O programa TC foi anexado a saida de h2:eth1."; \
	echo "2. h2 tentou iniciar uma conexao TCP com a porta 80."; \
	echo "3. TC_ACT_SHOT descartou o pacote antes de ele sair de h2."; \
	echo "4. A conexao nao foi estabelecida."; \
	echo; \
	echo "CONCLUSAO"; \
	echo "  [SUCESSO] O TC bloqueou TCP/80 no egress de h2."; \
	echo "  XDP atuou no ingresso; TC atuou na saida."; \
	echo

# Encerra somente o experimento XDP, preservando containers e enderecos para
# que o aluno possa iniciar o experimento TC sem recriar toda a topologia.
detach-xdp: verify-env
# Consulta primeiro os detalhes da interface para distinguir entre remover o
# programa atual e informar que ele ja estava ausente.
	@if sudo ip netns exec h1 ip -details link show dev eth1 | grep -q 'prog/xdp'; then \
		sudo ip netns exec h1 ip link set dev eth1 xdpgeneric off; \
		echo "XDP desanexado de h1:eth1. O experimento XDP foi encerrado."; \
	else \
		echo "XDP ja estava desanexado de h1:eth1."; \
	fi

# Encerra somente o experimento TC removendo o qdisc clsact e, com ele, o
# classificador eBPF anexado ao egress de h2.
detach-tc: verify-env
# Procura o qdisc clsact antes de remove-lo, evitando tratar como erro o estado
# em que o classificador TC ja esta desanexado.
	@if sudo ip netns exec h2 tc qdisc show dev eth1 | grep -q clsact; then \
		sudo ip netns exec h2 tc qdisc del dev eth1 clsact; \
		echo "TC desanexado de h2:eth1. O experimento TC foi encerrado."; \
	else \
		echo "TC ja estava desanexado de h2:eth1."; \
	fi

# Recupera o ambiente caso uma execucao anterior tenha sido interrompida.
# Depois do Containerlab, verifica h1 e h2 individualmente com docker inspect.
# Uma falha do destroy so e tolerada quando nenhum container permanece; caso
# contrario, orienta o aluno e interrompe antes de anunciar sucesso.
destroy:
	@set -u; \
	sudo containerlab destroy -t topology.yml --cleanup || true; \
	remaining_containers=""; \
	for container in "$(H1_CONTAINER)" "$(H2_CONTAINER)"; do \
		if sudo docker inspect "$$container" >/dev/null 2>&1; then \
			remaining_containers="$$remaining_containers $$container"; \
		fi; \
	done; \
	if [ -n "$$remaining_containers" ]; then \
		echo "ERRO: nao foi possivel remover completamente o Lab 2."; \
		echo "Os seguintes containers ainda existem:"; \
		for container in $$remaining_containers; do echo "  $$container"; done; \
		echo "Execute novamente:"; \
		echo "  sudo containerlab destroy -t topology.yml --cleanup"; \
		echo "Depois confira:"; \
		echo "  sudo docker ps -a --filter name=clab-ebpf-lab2"; \
		exit 1; \
	fi; \
	sudo rm -f /var/run/netns/h1 /var/run/netns/h2

clean: destroy
# Remove os objetos somente depois que destroy confirmou o fim da topologia.
	@rm -f xdp.o tc.o
# Esta mensagem so aparece quando containers, namespaces e objetos foram
# removidos com sucesso.
	@echo "Topologia e objetos do Lab 2 removidos."
