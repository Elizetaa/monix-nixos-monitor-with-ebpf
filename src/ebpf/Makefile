# Makefile - cpu_alert (eBPF + loader em userspace), pensado para NixOS
#
# No NixOS não há /usr/include nem apt: as ferramentas e headers vêm do
# shell.nix (rode "nix-shell" ou "make shell" antes). Este Makefile também
# continua funcionando em Debian/Ubuntu.
#
# Alvos:
#   make            compila tudo (cpu_alert.bpf.o + cpu_alert)
#   make run        compila e executa (usa sudo se não for root)
#   make shell      entra no ambiente Nix (nix-shell)
#   make doctor     diagnosticaexi ferramentas, tracefs e BTF
#   make clean      remove os arquivos gerados
#
# Variáveis (linha de comando): THRESHOLD=80  INTERVAL=1  CLANG=clang
# Exemplo: make run THRESHOLD=60 INTERVAL=2

CLANG      ?= clang
CC         ?= gcc
PKG_CONFIG ?= pkg-config

# Arquitetura alvo do BPF, detectada via uname -m
UNAME_M := $(shell uname -m)
ifeq ($(UNAME_M),x86_64)
  BPF_ARCH := x86
else ifeq ($(UNAME_M),aarch64)
  BPF_ARCH := arm64
else ifeq ($(UNAME_M),armv7l)
  BPF_ARCH := arm
else ifeq ($(UNAME_M),riscv64)
  BPF_ARCH := riscv
else ifeq ($(UNAME_M),s390x)
  BPF_ARCH := s390
else ifeq ($(UNAME_M),ppc64le)
  BPF_ARCH := powerpc
else
  BPF_ARCH := $(UNAME_M)
endif

# libbpf via pkg-config (funciona no Nix e nas outras distros)
LIBBPF_CFLAGS := $(shell $(PKG_CONFIG) --cflags libbpf 2>/dev/null)
LIBBPF_LIBS   := $(shell $(PKG_CONFIG) --libs libbpf 2>/dev/null || echo -lbpf)

# Debian/Ubuntu: <asm/types.h> fica em /usr/include/<multiarch>.
# No NixOS esse diretório não existe, então nada é adicionado.
MULTIARCH := $(shell $(CC) -print-multiarch 2>/dev/null)
ARCH_INC  := $(if $(MULTIARCH),$(if $(wildcard /usr/include/$(MULTIARCH)),-I/usr/include/$(MULTIARCH),),)

# No NixOS o shell.nix exporta BPF_EXTRA_CFLAGS=-I<linuxHeaders>/include
BPF_EXTRA_CFLAGS ?=

BPF_CFLAGS := -O2 -g -target bpf -D__TARGET_ARCH_$(BPF_ARCH) \
              -fno-stack-protector $(LIBBPF_CFLAGS) $(ARCH_INC) $(BPF_EXTRA_CFLAGS)
CFLAGS     ?= -O2 -Wall -Wextra
LDLIBS     := $(LIBBPF_LIBS)

THRESHOLD ?= 80
INTERVAL  ?= 1

# Só usa sudo se o usuário atual não for root
SUDO := $(if $(filter 0,$(shell id -u)),,sudo)

.PHONY: all run shell doctor clean help tools

all: cpu_alert cpu_alert.bpf.o

# Mensagem amigável se as ferramentas não estiverem no PATH (ex.: fora do nix-shell)
tools:
	@command -v $(CLANG) >/dev/null 2>&1 || { \
	  echo "erro: '$(CLANG)' não encontrado no PATH."; \
	  echo "No NixOS, rode 'nix-shell' (ou 'make shell') e tente de novo."; \
	  exit 1; }
	@$(PKG_CONFIG) --exists libbpf 2>/dev/null || { \
	  echo "erro: libbpf não encontrada via pkg-config."; \
	  echo "No NixOS, rode 'nix-shell' (ou 'make shell') e tente de novo."; \
	  exit 1; }

# Lado kernel: C -> bytecode eBPF
cpu_alert.bpf.o: cpu_alert.bpf.c | tools
	$(CLANG) $(BPF_CFLAGS) -c $< -o $@

# Lado userspace: loader normal em C, ligado com libbpf
cpu_alert: cpu_alert.c | tools
	$(CC) $(CFLAGS) $(LIBBPF_CFLAGS) -o $@ $< $(LDLIBS)

# O loader abre "cpu_alert.bpf.o" no diretório atual.
run: all
	$(SUDO) ./cpu_alert $(THRESHOLD) $(INTERVAL)

shell:
	nix-shell

doctor:
	@echo "== Ferramentas =="
	@if command -v $(CLANG) >/dev/null 2>&1; then echo "ok    clang: $$($(CLANG) --version | head -1)"; else echo "FALTA clang (use nix-shell)"; fi
	@if $(PKG_CONFIG) --exists libbpf 2>/dev/null; then echo "ok    libbpf: $$($(PKG_CONFIG) --modversion libbpf)"; else echo "FALTA libbpf (use nix-shell)"; fi
	@echo "== Kernel =="
	@if [ -e /sys/kernel/tracing/events/sched/sched_switch/id ]; then echo "ok    tracefs montado"; else echo "FALTA tracefs: sudo mount -t tracefs tracefs /sys/kernel/tracing"; fi
	@if [ -e /sys/kernel/btf/vmlinux ]; then echo "ok    BTF do kernel disponível"; else echo "aviso BTF ausente (não é necessário para este projeto)"; fi
	@echo "== Flags BPF =="
	@echo "$(BPF_CFLAGS)"

clean:
	rm -f cpu_alert cpu_alert.bpf.o

help:
	@echo "make | make run | make shell | make doctor | make clean"
	@echo "Variáveis: THRESHOLD=$(THRESHOLD) INTERVAL=$(INTERVAL) CLANG=$(CLANG)"