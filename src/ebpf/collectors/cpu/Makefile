# Este Makefile fica em collectors/cpu; os artefatos compartilhados ficam na raiz.
ROOT := ../..
BUILD_DIR := $(ROOT)/build
CPU_APP := $(BUILD_DIR)/cpu_alert
CPU_BPF := $(BUILD_DIR)/cpu_alert.bpf.o
CLANG ?= clang
CC ?= gcc
PKG_CONFIG ?= pkg-config
THRESHOLD ?= 80
INTERVAL ?= 1
NIX_SHELL ?= nix-shell
SHELL_FILE ?= $(ROOT)/shell.nix
SETUP_CMD ?= $(NIX_SHELL) $(SHELL_FILE)

# O nome esperado por __TARGET_ARCH_* difere de uname em algumas arquiteturas.
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

# pkg-config fornece os caminhos de headers e bibliotecas da libbpf.
LIBBPF_CFLAGS := $(shell $(PKG_CONFIG) --cflags libbpf 2>/dev/null)
LIBBPF_LIBS := $(shell $(PKG_CONFIG) --libs libbpf 2>/dev/null || echo -lbpf)
# Debian/Ubuntu instala asm/types.h no diretório multiarch; no Nix usamos
# BPF_EXTRA_CFLAGS, exportado por shell.nix, para apontar aos headers Linux.
MULTIARCH := $(shell $(CC) -print-multiarch 2>/dev/null)
ARCH_INC := $(if $(MULTIARCH),$(if $(wildcard /usr/include/$(MULTIARCH)),-I/usr/include/$(MULTIARCH),),)
BPF_CFLAGS := -O2 -g -target bpf -D__TARGET_ARCH_$(BPF_ARCH) -fno-stack-protector $(LIBBPF_CFLAGS) $(ARCH_INC) $(BPF_EXTRA_CFLAGS)
CFLAGS ?= -O2 -Wall -Wextra
# O loader anexa um tracepoint; sudo só é inserido para usuários sem root.
SUDO := $(if $(filter 0,$(shell id -u)),,sudo)

.DEFAULT_GOAL := help
.PHONY: all help check setup run clean

all: $(CPU_APP) $(CPU_BPF)

help:
	@echo "Coletor CPU: make (ajuda) | check | setup | run | clean"
	@echo "Variáveis: THRESHOLD=$(THRESHOLD) INTERVAL=$(INTERVAL) CLANG=$(CLANG)"

# check também informa pré-requisitos do kernel. BTF é opcional para este
# programa; tracefs ajuda a inspecionar os tracepoints durante o debug.
check:
	@status=0; \
	if command -v $(CLANG) >/dev/null 2>&1; then echo "ok    $(CLANG): $$($(CLANG) --version | head -1)"; else echo "FALTA $(CLANG) (use make setup)"; status=1; fi; \
	if command -v $(CC) >/dev/null 2>&1; then echo "ok    $(CC) disponível"; else echo "FALTA $(CC)"; status=1; fi; \
	if $(PKG_CONFIG) --exists libbpf 2>/dev/null; then echo "ok    libbpf: $$($(PKG_CONFIG) --modversion libbpf)"; else echo "FALTA libbpf via pkg-config (use make setup)"; status=1; fi; \
	if [ -e /sys/kernel/tracing/events/sched/sched_switch/id ] || [ -e /sys/kernel/debug/tracing/events/sched/sched_switch/id ]; then echo "ok    tracepoint sched_switch visível no tracefs"; else echo "AVISO tracefs/sched_switch não visível; verifique tracefs e permissões do kernel"; fi; \
	if [ -e /sys/kernel/btf/vmlinux ]; then echo "ok    BTF do kernel disponível"; else echo "info  BTF ausente (não exigido por este coletor)"; fi; \
	if [ $$status -eq 0 ]; then \
	  echo "CPU: dependências disponíveis (arquitetura BPF: $(BPF_ARCH))."; \
	  echo "Flags BPF: $(BPF_CFLAGS)"; \
	fi; exit $$status

setup:
	@$(SETUP_CMD)

$(CPU_BPF): cpu_alert.bpf.c
	# Cria o diretório de saída antes da compilação do bytecode BPF.
	mkdir -p $(BUILD_DIR)
	$(CLANG) $(BPF_CFLAGS) -c $< -o $@

$(CPU_APP): cpu_alert.c
	mkdir -p $(BUILD_DIR)
	$(CC) $(CFLAGS) $(LIBBPF_CFLAGS) -o $@ $< $(LIBBPF_LIBS)

# O loader abre build/cpu_alert.bpf.o; execute-o a partir da raiz do módulo.
run: all
	cd $(ROOT) && $(SUDO) ./build/cpu_alert $(THRESHOLD) $(INTERVAL)

clean:
	rm -f $(CPU_APP) $(CPU_BPF)
	# O link é mantido pelo processo loader e desanexado ao terminar; não há pin para limpar.
	@echo "CPU: o programa eBPF é desanexado quando o processo termina; não há objetos pinados pelo coletor."
