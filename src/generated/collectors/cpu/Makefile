ROOT := ../..
BUILD_DIR := $(ROOT)/build
CPU_APP := $(BUILD_DIR)/cpu_usage
CPU_BPF := $(BUILD_DIR)/cpu_usage.bpf.o
VMLINUX := $(BUILD_DIR)/vmlinux.h
CLANG ?= clang
CC ?= cc
BPFTOOL ?= bpftool
PKG_CONFIG ?= pkg-config
BTF_FILE ?= /sys/kernel/btf/vmlinux
INTERVAL ?= 2
NIX_SHELL ?= nix-shell
SHELL_FILE ?= $(ROOT)/shell.nix
SETUP_CMD ?= $(NIX_SHELL) $(SHELL_FILE)

UNAME_M := $(shell uname -m)
BPF_ARCH := $(UNAME_M)
ifeq ($(UNAME_M),x86_64)
BPF_ARCH := x86
else ifeq ($(UNAME_M),aarch64)
BPF_ARCH := arm64
else ifeq ($(UNAME_M),armv7l)
BPF_ARCH := arm
else ifeq ($(UNAME_M),ppc64le)
BPF_ARCH := powerpc
else ifeq ($(UNAME_M),riscv64)
BPF_ARCH := riscv
else ifeq ($(UNAME_M),s390x)
BPF_ARCH := s390
endif

LIBBPF_CFLAGS := $(shell $(PKG_CONFIG) --cflags libbpf 2>/dev/null)
LIBBPF_LIBS := $(shell $(PKG_CONFIG) --libs libbpf 2>/dev/null || echo -lbpf)
CFLAGS ?= -O2 -g -Wall -Wextra -Werror
BPF_CFLAGS := -O2 -g -target bpf -D__TARGET_ARCH_$(BPF_ARCH) -fno-stack-protector -I$(BUILD_DIR) $(LIBBPF_CFLAGS) $(BPF_EXTRA_CFLAGS)
SUDO := $(if $(filter 0,$(shell id -u)),,sudo)

.DEFAULT_GOAL := help
.PHONY: all help check setup run clean
all: $(CPU_APP) $(CPU_BPF)

help:
	@echo "Coletor CPU: make (ajuda) | all | check | setup | run | clean"
	@echo "Variáveis: INTERVAL=$(INTERVAL) CLANG=$(CLANG) BPFTOOL=$(BPFTOOL)"

setup:
	@$(SETUP_CMD)

$(BUILD_DIR):
	mkdir -p $@

$(VMLINUX): | $(BUILD_DIR)
	$(BPFTOOL) btf dump file $(BTF_FILE) format c > $@.tmp
	mv $@.tmp $@

$(CPU_BPF): cpu_usage.bpf.c $(VMLINUX) | $(BUILD_DIR)
	$(CLANG) $(BPF_CFLAGS) -c $< -o $@

$(CPU_APP): cpu_usage.c | $(BUILD_DIR)
	$(CC) $(CFLAGS) $(LIBBPF_CFLAGS) -o $@ $< $(LIBBPF_LIBS) -lm

check:
	@command -v $(CLANG) >/dev/null || { echo "Falta clang"; exit 1; }
	@command -v $(CC) >/dev/null || { echo "Falta compilador C"; exit 1; }
	@command -v $(BPFTOOL) >/dev/null || { echo "Falta bpftool"; exit 1; }
	@$(PKG_CONFIG) --exists libbpf || { echo "Falta libbpf via pkg-config"; exit 1; }
	@test -r $(BTF_FILE) || { echo "BTF do kernel indisponível: $(BTF_FILE)"; exit 1; }

run: all
	$(SUDO) $(CPU_APP) --interval $(INTERVAL)

clean:
	rm -f $(CPU_APP) $(CPU_BPF) $(VMLINUX) $(VMLINUX).tmp
