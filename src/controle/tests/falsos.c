/* Backend SOMENTE do binário de testes. Nunca instalado pelo pacote. */
#define _GNU_SOURCE
#include "../controle.h"
#include <errno.h>
#include <grp.h>
#include <pwd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static struct passwd contas[] = {
    {.pw_name="root", .pw_uid=0, .pw_gid=0},
    {.pw_name="alice", .pw_uid=1001, .pw_gid=100},
    {.pw_name="bob", .pw_uid=1002, .pw_gid=100},
    {.pw_name="admin", .pw_uid=2000, .pw_gid=100},
    {.pw_name="primario", .pw_uid=2001, .pw_gid=10},
};
static struct passwd *por_nome(const char *nome)
{
    if (!strcmp(nome, "alias")) return &contas[1];
    for (size_t i=0; i<sizeof(contas)/sizeof(*contas); i++)
        if (!strcmp(nome, contas[i].pw_name)) return &contas[i];
    return NULL;
}
static struct passwd *por_uid(uid_t uid)
{
    for (size_t i=0; i<sizeof(contas)/sizeof(*contas); i++)
        if (uid == contas[i].pw_uid) return &contas[i];
    return NULL;
}
static struct group *por_grupo(const char *nome)
{
    static struct group g = {.gr_name="wheel", .gr_gid=10};
    return strcmp(nome, "wheel") ? NULL : &g;
}
int __wrap_getgrouplist(const char *nome, gid_t gid, gid_t *grupos, int *n)
{
    if (getenv("CONTROLE_TEST_NSS_FAIL")) return -1;
    grupos[0] = gid;
    *n = 1;
    if (!strcmp(nome, "admin")) { grupos[1] = 10; *n = 2; }
    return *n;
}
int __wrap_sessoes_ler(struct sessoes *s)
{
    *s = (struct sessoes){0};
    if (getenv("CONTROLE_TEST_LOGIND_FAIL")) return -EIO;
    FILE *f = fopen(getenv("CONTROLE_TEST_STATE"), "r");
    if (!f) return -errno;
    unsigned uid;
    int pid, atual;
    while (fscanf(f, "%u %d %d", &uid, &pid, &atual) == 3) {
        struct sessao *itens = realloc(s->itens, (s->total+1)*sizeof(*itens));
        if (!itens) abort();
        s->itens = itens;
        s->itens[s->total++] = (struct sessao){uid, pid, atual};
    }
    fclose(f);
    return 0;
}

int __wrap_getpwnam_r(const char *n, struct passwd *p, char *b, size_t z, struct passwd **r)
{
    (void)b; (void)z;
    struct passwd *encontrado = por_nome(n);
    if (encontrado) *p = *encontrado;
    *r = encontrado ? p : NULL;
    return 0;
}
int __wrap_getpwuid_r(uid_t uid, struct passwd *p, char *b, size_t z, struct passwd **r)
{
    (void)b; (void)z;
    struct passwd *encontrado = por_uid(uid);
    if (encontrado) *p = *encontrado;
    *r = encontrado ? p : NULL;
    return 0;
}
int __wrap_getgrnam_r(const char *n, struct group *g, char *b, size_t z, struct group **r)
{
    (void)b; (void)z;
    struct group *encontrado = por_grupo(n);
    if (encontrado) *g = *encontrado;
    *r = encontrado ? g : NULL;
    return 0;
}
