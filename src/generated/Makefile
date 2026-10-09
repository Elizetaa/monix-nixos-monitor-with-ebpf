# Biblioteca/agente principal; observabilidade e compilação nativa opcionais.
MODULES := cpu gpu memory storage thermal
PYTHON ?= $(shell command -v python3)
CONFIG ?= config/agent.toml
COLLECTOR ?=
EBPF ?=
INTERVAL ?=
LISTEN ?=
PORT ?=
EXTRA_ARGS ?=
SETUP_CMD ?= nix-shell shell.nix
SUDO := $(if $(filter 0,$(shell id -u)),,sudo)
RUN_BUILD := $(if $(filter required,$(EBPF)),$(if $(filter-out all cpu,$(COLLECTOR)),,build),)
RUN_SUDO := $(if $(RUN_BUILD),$(SUDO),)
OPTIONS = $(if $(COLLECTOR),--collector $(COLLECTOR)) $(if $(EBPF),--ebpf $(EBPF)) $(if $(INTERVAL),--interval $(INTERVAL)) $(if $(LISTEN),--listen $(LISTEN)) $(if $(PORT),--port $(PORT))

.DEFAULT_GOAL := help
.PHONY: all help check check-native setup build run exporter snapshot sample test stack-up stack-down stack-native clean $(MODULES)

all: help

help:
	@echo "Monix: biblioteca Python + agente JSON local"
	@echo "  make setup        abre o ambiente Nix"
	@echo "  make check        verifica Python, importação e configuração do agente"
	@echo "  make check-native verifica dependências do coletor eBPF"
	@echo "  make build        compila o programa eBPF e seu loader"
	@echo "  make run          inicia a API JSON, com valores de CONFIG=$(CONFIG)"
	@echo "  make exporter     habilita também /metrics no mesmo snapshot"
	@echo "  make snapshot     imprime um snapshot JSON real e encerra"
	@echo "  make sample       imprime um snapshot sem privilégios via procfs/sysfs"
	@echo "  make test         executa testes sem root e sem hardware real"
	@echo "  make stack-up     inicia Prometheus/Grafana pelo Docker Compose"
	@echo "  make stack-native inicia Prometheus/Grafana locais (inclusive NixOS)"
	@echo "  make stack-down   encerra o Compose, preservando seus volumes"
	@echo "  make clean        remove somente os artefatos de compilação"
	@echo "Variáveis opcionais sobrescrevem TOML: COLLECTOR=... EBPF=required|auto|disabled"
	@echo "           INTERVAL=... LISTEN=... PORT=... CONFIG=... EXTRA_ARGS='...'"
	@echo "make run EBPF=required compila o coletor eBPF e usa sudo."

check:
	PYTHONPATH="$(abspath ..)" $(PYTHON) -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ necessário"; from generated import Monitor; from generated.configuration import load_config; load_config("$(CONFIG)"); print("Biblioteca e configuração disponíveis.")'

check-native:
	@$(MAKE) --no-print-directory -C collectors/cpu check

setup:
	@$(SETUP_CMD)

build:
	@$(MAKE) --no-print-directory -C collectors/cpu all

run: $(RUN_BUILD)
	$(RUN_SUDO) env PYTHONPATH="$(abspath ..)" $(PYTHON) -m generated --config "$(CONFIG)" $(OPTIONS) $(EXTRA_ARGS)

exporter: $(RUN_BUILD)
	$(RUN_SUDO) env PYTHONPATH="$(abspath ..)" $(PYTHON) -m generated --config "$(CONFIG)" $(OPTIONS) --prometheus $(EXTRA_ARGS)

snapshot: $(RUN_BUILD)
	$(RUN_SUDO) env PYTHONPATH="$(abspath ..)" $(PYTHON) -m generated --config "$(CONFIG)" $(OPTIONS) --once $(EXTRA_ARGS)

sample:
	$(PYTHON) exporter.py --config "$(CONFIG)" $(OPTIONS) --once --ebpf disabled $(EXTRA_ARGS)

test:
	PYTHONPATH="$(abspath ..)" PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m unittest discover -s tests -v

stack-up:
	docker compose --project-directory . up -d

stack-down:
	docker compose --project-directory . down

stack-native:
	bash scripts/start-stack.sh

clean:
	@$(MAKE) --no-print-directory -C collectors/cpu clean

$(MODULES):
	@$(MAKE) --no-print-directory -C collectors/$@ help
