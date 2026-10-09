# Incluído pelos coletores baseados nas interfaces de hardware do Linux.
ROOT := ../..
PYTHON ?= $(shell command -v python3)
CONFIG ?= config/agent.toml
INTERVAL ?=
PORT ?=
EXTRA_ARGS ?=
SETUP_CMD ?= nix-shell $(ROOT)/shell.nix

.DEFAULT_GOAL := help
.PHONY: all help check setup run clean

all: help
help:
	@echo "Coletor $(COLLECTOR): make check | setup | run | clean"
check:
	@$(PYTHON) -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ necessário"; print("$(COLLECTOR): Python disponível; dados opcionais dependem do hardware/driver.")'
setup:
	@$(SETUP_CMD)
run:
	cd $(ROOT) && $(PYTHON) exporter.py --config "$(CONFIG)" --collector $(COLLECTOR) $(if $(INTERVAL),--interval $(INTERVAL)) $(if $(PORT),--port $(PORT)) $(EXTRA_ARGS)
clean:
	@echo "$(COLLECTOR): não produz artefatos compilados nem objetos pinados."
