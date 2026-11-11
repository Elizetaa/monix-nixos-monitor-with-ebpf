#define _GNU_SOURCE
#include "controle.h"
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <systemd/sd-bus.h>
#include <systemd/sd-login.h>
#include <time.h>
#include <unistd.h>

#ifndef CONTROLE_RUN
#define CONTROLE_RUN "/run/controle-pam"
#endif
#ifndef CONTROLE_OWNER
#define CONTROLE_OWNER 0
#endif

int sessoes_travar(void)
{
    int dir = open(CONTROLE_RUN, O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dir < 0) return -errno;
    struct stat st;
    int fd = -1, erro = -EPERM;
    if (fstat(dir, &st) < 0 || st.st_uid != CONTROLE_OWNER ||
        (st.st_mode & 0777) != 0700) goto fim;
    fd = openat(dir, "admissao.lock", O_CREAT | O_RDWR | O_NOFOLLOW |
                O_CLOEXEC | O_NONBLOCK, 0600);
    if (fd < 0) { erro = -errno; goto fim; }
    if (fstat(fd, &st) < 0 || !S_ISREG(st.st_mode) || st.st_nlink != 1 ||
        st.st_uid != CONTROLE_OWNER || (st.st_mode & 0777) != 0600) goto fim;
    /* Limite de 5 s: uma pilha PAM travada não bloqueia novos pedidos para sempre. */
    for (int i = 0; i < 250; i++) {
        if (flock(fd, LOCK_EX | LOCK_NB) == 0) { close(dir); return fd; }
        if (errno != EWOULDBLOCK && errno != EINTR) { erro = -errno; goto fim; }
        struct timespec pausa = { .tv_nsec = 20000000 };
        nanosleep(&pausa, NULL);
    }
    erro = -ETIMEDOUT;
fim:
    if (fd >= 0) close(fd);
    close(dir);
    return erro;
}

int sessoes_ler(struct sessoes *s)
{
    sd_bus *bus = NULL;
    sd_bus_message *lista = NULL;
    char *atual = NULL;
    *s = (struct sessoes){0};
    int r = sd_bus_open_system(&bus);
    if (r < 0) goto fim;
    /* Consulta ao serviço vivo: diretório vazio ou cache antigo não prova ausência. */
    r = sd_bus_call_method(bus, "org.freedesktop.login1", "/org/freedesktop/login1",
        "org.freedesktop.login1.Manager", "ListSessions", NULL, &lista, "");
    if (r < 0) goto fim;
    if (!sd_bus_message_has_signature(lista, "a(susso)")) { r = -EPROTO; goto fim; }
    r = sd_pid_get_session(0, &atual);
    if (r < 0 && r != -ENODATA && r != -ENXIO) goto fim;
    r = sd_bus_message_enter_container(lista, 'a', "(susso)");
    if (r <= 0) { if (!r) r = -EPROTO; goto fim; }
    const char *id, *nome, *assento, *caminho;
    uint32_t uid;
    while ((r = sd_bus_message_read(lista, "(susso)", &id, &uid, &nome,
                                    &assento, &caminho)) > 0) {
        char *classe = NULL;
        r = sd_bus_get_property_string(bus, "org.freedesktop.login1", caminho,
            "org.freedesktop.login1.Session", "Class", NULL, &classe);
        if (r < 0) goto fim;
        /* Não contar greeter, desbloqueador, gerenciador de usuário e tarefas batch. */
        int usuario = !strcmp(classe, "user") || !strncmp(classe, "user-", 5);
        int interna = !strcmp(classe, "greeter") || !strcmp(classe, "lock-screen") ||
            !strcmp(classe, "background") || !strcmp(classe, "background-light") ||
            !strcmp(classe, "manager") || !strcmp(classe, "manager-early") ||
            !strcmp(classe, "none");
        free(classe);
        if (!usuario && !interna) { r = -EPROTO; goto fim; }
        if (!usuario) continue;
        uint32_t lider;
        r = sd_bus_get_property_trivial(bus, "org.freedesktop.login1", caminho,
            "org.freedesktop.login1.Session", "Leader", NULL, 'u', &lider);
        if (r < 0) goto fim;
        struct sessao *itens = realloc(s->itens, (s->total + 1) * sizeof(*itens));
        if (!itens) { r = -ENOMEM; goto fim; }
        s->itens = itens;
        s->itens[s->total++] = (struct sessao){uid, (pid_t)lider,
                                             atual && !strcmp(atual, id)};
    }
fim:
    free(atual);
    sd_bus_message_unref(lista);
    sd_bus_unref(bus);
    if (r < 0) { free(s->itens); *s = (struct sessoes){0}; }
    return r < 0 ? r : 0;
}
