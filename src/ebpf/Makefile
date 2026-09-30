# Orquestrador dos coletores eBPF.
MODULES := cpu gpu memory storage thermal
MODULE_DIR := collectors
NIX_SHELL ?= nix-shell
SHELL_FILE ?= shell.nix
SETUP_CMD ?= $(NIX_SHELL) $(SHELL_FILE)
COLLECTOR ?= all
THRESHOLD ?= 80
INTERVAL ?= 1

.PHONY: all help check setup run clean $(MODULES)

all: help

help:
	@echo "Uso: make <alvo> [COLLECTOR=cpu|gpu|memory|storage|thermal|all]"
	@echo "  make check   verifica dependências de todos os coletores"
	@echo "  make setup   inicia o shell Nix (SETUP_CMD=... para parametrizar)"
	@echo "  make run     executa coletores (COLLECTOR=cpu; THRESHOLD=80 INTERVAL=1)"
	@echo "  make clean   limpa artefatos e recursos de todos os coletores"

check:
	@status=0; for module in $(MODULES); do \
	  echo "== $$module =="; \
	  if $(MAKE) --no-print-directory -C $(MODULE_DIR)/$$module check; then :; \
	  else status=1; fi; \
	done; \
	if [ $$status -eq 0 ]; then echo "Resumo: todos os módulos estão prontos."; \
	else echo "Resumo: há dependências ausentes ou módulos não implementados."; fi; \
	exit $$status

setup:
	@$(SETUP_CMD)

run:
	@if [ "$(COLLECTOR)" = all ]; then \
	  echo "Informe COLLECTOR=cpu para executar um coletor implementado."; exit 2; \
	fi
	@$(MAKE) --no-print-directory -C $(MODULE_DIR)/$(COLLECTOR) run THRESHOLD=$(THRESHOLD) INTERVAL=$(INTERVAL)

clean:
	@status=0; for module in $(MODULES); do \
	  echo "== limpando $$module =="; \
	  if $(MAKE) --no-print-directory -C $(MODULE_DIR)/$$module clean; then :; \
	  else status=1; fi; \
	done; exit $$status
