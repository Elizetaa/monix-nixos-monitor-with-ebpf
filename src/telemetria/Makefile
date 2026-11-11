PYTHON ?= $(shell command -v python3)
CC ?= cc
CLANG ?= clang
BPFTOOL ?= bpftool
PKG_CONFIG ?= pkg-config
BTF_FILE ?= /sys/kernel/btf/vmlinux
CFLAGS ?= -O2 -g -Wall -Wextra -Werror
LIBBPF_CFLAGS := $(shell $(PKG_CONFIG) --cflags libbpf 2>/dev/null)
LIBBPF_LIBS := $(shell $(PKG_CONFIG) --libs libbpf 2>/dev/null)
INTERVAL ?= 2
PORT ?= 9108

.DEFAULT_GOAL := build
.PHONY: build check-native run test validate clean
BPF_MODULES := cpu_usage memoria paginas io zonas drm filesystems
BPF_OBJECTS := $(addprefix build/,$(addsuffix .bpf.o,$(BPF_MODULES)))

build: build/cpu_usage build/observador $(BPF_OBJECTS)

check-native:
	command -v $(CLANG)
	command -v $(CC)
	command -v $(BPFTOOL)
	$(PKG_CONFIG) --exists libbpf
	test -r $(BTF_FILE)

build/.dir:
	mkdir -p build
	touch $@

build/vmlinux.h: | build/.dir
	$(BPFTOOL) btf dump file $(BTF_FILE) format c > $@.tmp
	mv $@.tmp $@

build/%.bpf.o: coletores/ebpf/%.bpf.c build/vmlinux.h $(wildcard coletores/ebpf/*.bpf.h) coletores/ebpf/metricas.h
	$(CLANG) -O2 -g -target bpf -fno-stack-protector -Ibuild $(LIBBPF_CFLAGS) -c $< -o $@

build/cpu_usage: coletores/ebpf/cpu_usage.c | build/.dir
	$(CC) $(CFLAGS) $(LIBBPF_CFLAGS) $< -o $@ $(LIBBPF_LIBS) -lm

build/observador: coletores/ebpf/observador.c coletores/ebpf/metricas.h | build/.dir
	$(CC) $(CFLAGS) $(LIBBPF_CFLAGS) $< -o $@ $(LIBBPF_LIBS) -lm

run:
	PYTHONPATH="$(abspath ..)" $(PYTHON) -m telemetria --interval $(INTERVAL) --port $(PORT)

test:
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$(abspath ..)" $(PYTHON) -m unittest discover -s tests -v

validate:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) tests/validar_host.py

clean:
	rm -rf build
