# Usa Bash para executar as receitas. O alvo attach utiliza recursos como funcoes,
# trap e pipefail, que nao fazem parte de todos os shells POSIX.
SHELL := /bin/bash

# gcc -print-multiarch descobre o diretorio dos headers da arquitetura atual.
# Em x86-64, por exemplo, o resultado costuma ser /usr/include/x86_64-linux-gnu.
# Esse caminho e necessario para que o Clang encontre headers como asm/types.h.
ARCH_INCLUDE := /usr/include/$(shell gcc -print-multiarch 2>/dev/null)

# Diretorio do bpffs no qual programas e links do laboratorio serao fixados.
# O pinning mantem referencias aos objetos eBPF enquanto o teste esta ativo.
PIN_ROOT := /sys/fs/bpf/ebpf_lab1

# Estes nomes representam acoes, e nao arquivos que devem ser gerados.
# A declaracao PHONY garante que os alvos sejam executados mesmo se existir um
# arquivo chamado help, check, mounts, build, setup, attach, detach ou clean no
# diretorio.
.PHONY: help check mounts build setup attach detach clean

# Exibe os comandos disponiveis sem preparar ou modificar o ambiente.
help:
# Identifica o laboratorio antes de apresentar sua interface publica.
	@echo "Lab 1 - Kprobe e BPF LSM"
# Documenta os alvos que o aluno pode executar e a responsabilidade de cada um.
	@echo "  make check   verifica as dependencias do laboratorio"
	@echo "  make mounts  prepara bpffs e securityfs"
	@echo "  make build   gera vmlinux.h e compila os programas"
	@echo "  make setup   prepara filesystems e compila os programas"
	@echo "  make attach  carrega e anexa os programas ja preparados"
	@echo "  make detach  desanexa os programas do kernel"
	@echo "  make clean   remove somente os arquivos gerados"

# Verifica primeiro as ferramentas usadas na compilacao e na inspecao eBPF.
# command -v termina com erro quando um executavel nao esta no PATH.
# A ultima verificacao confirma a disponibilidade do BTF do kernel, necessario
# para gerar o header vmlinux.h utilizado pelo programa BPF LSM.
check:
# Confirma o Clang, que transforma os dois programas C em bytecode eBPF.
	@command -v clang >/dev/null || { \
		echo "ERRO: Clang nao foi encontrado; sem ele os programas eBPF nao podem ser compilados."; \
		echo "Instale com: sudo apt update && sudo apt install -y clang llvm"; \
		echo "Depois, execute novamente: make check"; exit 1; }
# Confirma o GCC, usado apenas para descobrir o diretorio dos headers da
# arquitetura por meio de gcc -print-multiarch.
	@command -v gcc >/dev/null || { \
		echo "ERRO: GCC nao foi encontrado; ele e usado para descobrir os headers da arquitetura."; \
		echo "Instale com: sudo apt install -y build-essential"; \
		echo "Depois, execute novamente: make check"; exit 1; }
# Confirma o bpftool, necessario tanto para gerar vmlinux.h quanto para carregar
# e anexar os programas ao kernel.
	@command -v bpftool >/dev/null || { \
		echo "ERRO: bpftool nao foi encontrado; ele e necessario para gerar vmlinux.h e carregar eBPF."; \
		echo "Instale com: sudo apt install -y bpftool"; \
		echo "Consulte tambem a preparacao do ambiente no README principal."; exit 1; }
# Confirma o mountpoint, usado para evitar montagens duplicadas de bpffs e
# securityfs.
	@command -v mountpoint >/dev/null || { \
		echo "ERRO: mountpoint nao foi encontrado; nao e possivel verificar bpffs e securityfs."; \
		echo "Instale com: sudo apt install -y util-linux"; \
		echo "Depois, execute novamente: make check"; exit 1; }
# Verifica o BTF exposto pelo kernel. Sem ele nao e possivel gerar o vmlinux.h
# que descreve os tipos usados pelo programa BPF LSM.
	@test -r /sys/kernel/btf/vmlinux || { \
		echo "ERRO: /sys/kernel/btf/vmlinux nao esta disponivel."; \
		echo "No PowerShell, execute 'wsl --update' e depois 'wsl --shutdown'."; \
		exit 1; \
	}
# Informa sucesso somente depois que ferramentas e BTF foram encontrados.
	@echo "Dependencias basicas do Lab 1 encontradas."

# Prepara os dois filesystems virtuais usados pelo laboratorio:
#
# - bpffs: permite fixar programas, links e maps eBPF em /sys/fs/bpf;
# - securityfs: expoe informacoes dos Linux Security Modules em
#   /sys/kernel/security, incluindo a lista de LSMs ativos.
#
# mountpoint evita montar novamente um filesystem que ja esteja pronto.
# A falha de securityfs gera apenas um aviso porque o Kprobe pode funcionar sem
# ele; nesse caso, somente o teste opcional do BPF LSM fica indisponivel.
mounts:
# Monta bpffs somente quando necessario. Esse filesystem permite fixar os
# programas e links eBPF para mante-los referenciados durante o experimento.
	@if ! mountpoint -q /sys/fs/bpf; then \
		echo "Preparando o BPF filesystem em /sys/fs/bpf..."; \
		sudo mkdir -p /sys/fs/bpf; \
		sudo mount -t bpf bpf /sys/fs/bpf; \
	fi
# Monta securityfs somente quando necessario. A falha gera aviso, e nao erro,
# porque o Kprobe ainda funciona mesmo sem a verificacao opcional do BPF LSM.
	@if ! mountpoint -q /sys/kernel/security; then \
		echo "Preparando o securityfs em /sys/kernel/security..."; \
		sudo mkdir -p /sys/kernel/security; \
		if ! sudo mount -t securityfs securityfs /sys/kernel/security; then \
			echo "AVISO: nao foi possivel montar securityfs; a verificacao do BPF LSM pode ficar indisponivel."; \
		fi; \
	fi

# O alvo build depende de check: as dependencias sao verificadas antes da
# compilacao. Primeiro, bpftool converte o BTF do kernel em vmlinux.h. Depois,
# Clang gera bytecode eBPF (-target bpf) para cada programa do laboratorio.
#
# -D__TARGET_ARCH_x86 informa a arquitetura esperada pelas macros da libbpf.
# -O2 gera codigo adequado ao verifier e -g inclui informacoes BTF de depuracao.
build: check
# Sinaliza o inicio da geracao dos arquivos para separar essa etapa de check.
	@echo "Gerando vmlinux.h e compilando os programas..."
# Converte o BTF do kernel em declaracoes C usadas para compilar com CO-RE.
	@bpftool btf dump file /sys/kernel/btf/vmlinux format c > vmlinux.h
# Compila o Kprobe para bytecode eBPF. O include de arquitetura resolve headers
# como asm/types.h, e -O2 produz codigo adequado ao verifier.
	@clang -O2 -g -target bpf -D__TARGET_ARCH_x86 \
		-I"$(ARCH_INCLUDE)" \
		-c kprobe_exec.bpf.c -o kprobe_exec.bpf.o || { \
			echo "ERRO: falha ao compilar kprobe_exec.bpf.c."; \
			echo "Leia o diagnostico do Clang acima e confirme os pacotes clang, libbpf-dev e headers."; \
			exit 1; }
# Compila o BPF LSM incluindo o diretorio atual, onde vmlinux.h acabou de ser
# gerado, alem dos headers especificos da arquitetura.
	@clang -O2 -g -target bpf -D__TARGET_ARCH_x86 \
		-I"$(CURDIR)" -I"$(ARCH_INCLUDE)" \
		-c lsm_block.bpf.c -o lsm_block.bpf.o || { \
			echo "ERRO: falha ao compilar lsm_block.bpf.c."; \
			echo "Leia o diagnostico do Clang acima e confirme se vmlinux.h foi gerado corretamente."; \
			exit 1; }
# Confirma que os dois objetos foram gerados sem esconder erros do Clang.
	@echo "Programas eBPF compilados."

# Prepara todos os recursos necessarios sem carregar os programas no kernel.
setup: check mounts build
# Orienta a proxima etapa apenas depois que dependencias, filesystems e objetos
# estiverem prontos.
	@echo "Ambiente do Lab 1 preparado. Execute: make attach"

# Carrega e anexa os objetos produzidos anteriormente por setup.
#
# set -euo pipefail interrompe a execucao ao encontrar erros, variaveis nao
# definidas ou falhas escondidas em pipelines. Se o carregamento falhar, rollback
# remove os pins criados nessa tentativa sem apagar os objetos compilados.
#
# bpftool prog loadall carrega o objeto no kernel. A opcao autoattach interpreta
# SEC("kprobe/__x64_sys_execve") e cria o link entre o programa e o hook.
# O link permanece fixado no bpffs ate a execucao de make detach.
#
# Em seguida, /sys/kernel/security/lsm e consultado. O programa LSM so e
# carregado quando "bpf" aparece entre os LSMs ativos. Se estiver disponivel,
# curl --version funciona como teste: o retorno do programa eBPF deve negar a
# execucao. Se BPF LSM nao estiver ativo, o Kprobe continua sendo testavel.
attach:
# Impede o carregamento quando setup ainda nao produziu os dois objetos.
	@test -f kprobe_exec.bpf.o -a -f lsm_block.bpf.o || { \
		echo "ERRO: os programas do Lab 1 ainda nao foram preparados."; \
		echo "Execute primeiro: make setup"; exit 1; }
# Evita sobrepor pins de uma execucao anterior. O Kprobe e o BPF LSM permanecem
# ativos depois que o alvo termina, permitindo que o aluno execute os testes em
# outros terminais ate chamar make detach.
	@set -Eeuo pipefail; \
	if test -e "$(PIN_ROOT)"; then \
		echo "ERRO: os programas do Lab 1 ja parecem estar anexados."; \
		echo "Execute primeiro: make detach"; \
		exit 1; \
	fi; \
	rollback() { \
		sudo rm -rf "$(PIN_ROOT)" || true; \
	}; \
	on_signal() { \
		echo "AVISO: o carregamento foi interrompido; removendo os pins criados nesta tentativa."; \
		rollback; \
		exit 130; \
	}; \
	on_error() { \
		status=$$?; \
		echo "ERRO: nao foi possivel anexar os programas eBPF do Lab 1."; \
		echo "Leia a mensagem imediatamente acima para identificar a causa."; \
		echo "Os pins criados nesta tentativa serao removidos; os arquivos compilados serao preservados."; \
		rollback; \
		echo "Consulte 'Solucao de problemas' no README do Lab 1."; \
		exit $$status; \
	}; \
	trap on_signal INT TERM; \
	trap on_error ERR; \
	echo "=== LAB 1: Kprobe vs. BPF LSM (WSL 2/x86-64) ==="; \
	sudo mkdir -p "$(PIN_ROOT)/kprobe"; \
	echo "Anexando o Kprobe a __x64_sys_execve..."; \
	sudo bpftool prog loadall kprobe_exec.bpf.o "$(PIN_ROOT)/kprobe" autoattach; \
	echo "Kprobe carregado. Depois que este comando terminar, observe neste terminal com:"; \
	echo "  sudo cat /sys/kernel/tracing/trace_pipe"; \
	echo "Verificando a disponibilidade do BPF LSM..."; \
	if sudo test -r /sys/kernel/security/lsm && sudo grep -qw bpf /sys/kernel/security/lsm; then \
		sudo mkdir -p "$(PIN_ROOT)/lsm"; \
		sudo bpftool prog loadall lsm_block.bpf.o "$(PIN_ROOT)/lsm" autoattach; \
		if curl --version >/dev/null 2>&1; then \
			echo "FALHA: o curl foi executado mesmo com o BPF LSM carregado."; \
			false; \
		else \
			echo "SUCESSO: a execucao do curl foi bloqueada pelo BPF LSM."; \
		fi; \
	else \
		echo "AVISO: BPF LSM nao esta ativo neste kernel; o teste de bloqueio sera ignorado."; \
		echo "LSMs ativos: $$(sudo cat /sys/kernel/security/lsm 2>/dev/null || echo 'indisponivel')"; \
	fi; \
	trap - ERR INT TERM; \
	echo "Programas anexados. Quando terminar o experimento, execute: make detach"

# Remove os pins do bpffs, liberando os programas e links no kernel, mas
# preserva os artefatos locais para que make attach possa ser repetido.
detach:
	@sudo rm -rf "$(PIN_ROOT)"
	@echo "Programas eBPF do Lab 1 desanexados."

# Apaga somente os artefatos locais. Para evitar programas ativos sem os
# arquivos usados para cria-los, exige que make detach tenha sido executado.
clean:
	@if test -e "$(PIN_ROOT)"; then \
		echo "ERRO: os programas do Lab 1 ainda estao anexados."; \
		echo "Execute primeiro: make detach"; \
		exit 1; \
	fi
	@rm -f kprobe_exec.bpf.o lsm_block.bpf.o vmlinux.h
	@echo "Arquivos gerados pelo Lab 1 removidos."
