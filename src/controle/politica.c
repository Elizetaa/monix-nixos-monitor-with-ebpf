#define _DEFAULT_SOURCE
#include "controle.h"
#include <grp.h>
#include <pwd.h>
#include <stdlib.h>

int politica_admin(uid_t uid, const char *grupo)
{
    if (uid == 0) return 1;
    char buffer[16384];
    struct group gr, *g;
    if (getgrnam_r(grupo, &gr, buffer, sizeof(buffer), &g) || !g) return -1;
    gid_t admin = g->gr_gid;
    struct passwd conta, *pw;
    if (getpwuid_r(uid, &conta, buffer, sizeof(buffer), &pw) || !pw) return -1;
    if (pw->pw_gid == admin) return 1;
    /* getgrouplist inclui o grupo primário e consulta os suplementares via NSS. */
    int n = 16, resultado = -1;
    gid_t *grupos = malloc((size_t)n * sizeof(*grupos));
    if (!grupos) return -1;
    if (getgrouplist(pw->pw_name, pw->pw_gid, grupos, &n) < 0) {
        free(grupos);
        if (n <= 0 || n > 65536) return -1;
        grupos = malloc((size_t)n * sizeof(*grupos));
        if (!grupos) return -1;
        if (getgrouplist(pw->pw_name, pw->pw_gid, grupos, &n) < 0) goto fim;
    }
    resultado = 0;
    for (int i = 0; i < n; i++)
        if (grupos[i] == admin) resultado = 1;
fim:
    free(grupos);
    return resultado;
}

int politica_permitir(uid_t uid, const char *grupo, const struct sessoes *s)
{
    for (size_t i = 0; i < s->total; i++) {
        if (s->itens[i].uid == uid) continue;
        int admin = politica_admin(s->itens[i].uid, grupo);
        if (admin < 0) return -1;
        if (!admin) return 0;
    }
    return 1;
}
