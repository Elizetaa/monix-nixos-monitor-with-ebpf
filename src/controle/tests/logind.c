/* Serviço login1 mínimo em barramento PRIVADO: testa o leitor D-Bus de produção. */
#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <systemd/sd-bus.h>

static int listar(sd_bus_message *m, void *dados, sd_bus_error *erro)
{
    (void)dados; (void)erro;
    sd_bus_message *r = NULL;
    int e = sd_bus_message_new_method_return(m, &r);
    if (e >= 0) e = sd_bus_message_open_container(r, 'a', "(susso)");
    if (e >= 0) e = sd_bus_message_append(r, "(susso)", "controle_teste", 1001U,
                                        "alice", "", "/org/freedesktop/login1/session/teste");
    if (e >= 0) e = sd_bus_message_close_container(r);
    if (e >= 0) e = sd_bus_send(NULL, r, NULL);
    sd_bus_message_unref(r);
    return e < 0 ? e : 1;
}
static int propriedade(sd_bus *bus, const char *p, const char *i, const char *nome,
                       sd_bus_message *r, void *dados, sd_bus_error *erro)
{
    (void)bus; (void)p; (void)i; (void)dados; (void)erro;
    if (!strcmp(nome, "Class")) return sd_bus_message_append(r, "s", getenv("CLASSE"));
    return sd_bus_message_append(r, "u", 1234U);
}
static const sd_bus_vtable gerente[] = {
    SD_BUS_VTABLE_START(0), SD_BUS_METHOD("ListSessions", "", "a(susso)", listar, 0),
    SD_BUS_VTABLE_END
};
static const sd_bus_vtable sessao[] = {
    SD_BUS_VTABLE_START(0),
    SD_BUS_PROPERTY("Class", "s", propriedade, 0, 0),
    SD_BUS_PROPERTY("Leader", "u", propriedade, 0, 0),
    SD_BUS_VTABLE_END
};
int main(void)
{
    sd_bus *bus = NULL;
    int r = sd_bus_open_system(&bus);
    if (r >= 0) r = sd_bus_add_object_vtable(bus, NULL, "/org/freedesktop/login1",
                          "org.freedesktop.login1.Manager", gerente, NULL);
    if (r >= 0) r = sd_bus_add_object_vtable(bus, NULL,
        "/org/freedesktop/login1/session/teste", "org.freedesktop.login1.Session", sessao, NULL);
    if (r >= 0) r = sd_bus_request_name(bus, "org.freedesktop.login1", 0);
    if (r < 0) { fprintf(stderr, "%s\n", strerror(-r)); return 1; }
    puts("pronto"); fflush(stdout);
    for (;;) {
        r = sd_bus_process(bus, NULL);
        if (r < 0) return 2;
        if (!r) sd_bus_wait(bus, UINT64_MAX);
    }
}
