CC ?= cc
PKG_CONFIG ?= pkg-config
CFLAGS ?= -O2 -g -Wall -Wextra -Werror
CPPFLAGS += $(shell $(PKG_CONFIG) --cflags libsystemd)
LDLIBS += $(shell $(PKG_CONFIG) --libs libsystemd) -lpam
FONTES = pam_controle.c politica.c sessoes.c

.DEFAULT_GOAL := build
.PHONY: build test clean
build: build/pam_controle.so
build/.dir:
	mkdir -p build
	touch $@
build/pam_controle.so: $(FONTES) controle.h | build/.dir
	$(CC) $(CPPFLAGS) $(CFLAGS) -fPIC -shared -Wl,-z,defs,-z,relro,-z,now $(FONTES) -o $@ $(LDFLAGS) $(LDLIBS)
build/pam_teste.so: $(FONTES) controle.h tests/falsos.c | build/.dir
	$(CC) $(CPPFLAGS) $(CFLAGS) -fPIC -shared -DCONTROLE_RUN='"$(abspath build/estado)"' -DCONTROLE_OWNER=getuid\(\) $(FONTES) tests/falsos.c -Wl,--wrap=sessoes_ler,--wrap=getpwnam_r,--wrap=getpwuid_r,--wrap=getgrnam_r,--wrap=getgrouplist -o $@ $(LDFLAGS) $(LDLIBS)
build/pam_registro.so: tests/registro.c | build/.dir
	$(CC) $(CPPFLAGS) $(CFLAGS) -fPIC -shared $< -o $@ $(LDFLAGS) -lpam
build/cliente: tests/cliente.c | build/.dir
	$(CC) $(CPPFLAGS) $(CFLAGS) -Wl,--export-dynamic $< -o $@ $(LDFLAGS) -lpam
build/logind: tests/logind.c | build/.dir
	$(CC) $(CPPFLAGS) $(CFLAGS) $< -o $@ $(LDFLAGS) $(LDLIBS)
build/leitor: tests/leitor.c sessoes.c controle.h | build/.dir
	$(CC) $(CPPFLAGS) $(CFLAGS) tests/leitor.c sessoes.c -o $@ $(LDFLAGS) $(LDLIBS)
test: build/logind build/leitor build/pam_controle.so build/pam_teste.so build/pam_registro.so build/cliente
	PYTHONDONTWRITEBYTECODE=1 python3 tests/test_controle.py
	PYTHONDONTWRITEBYTECODE=1 python3 tests/test_logind.py
clean:
	rm -rf build
