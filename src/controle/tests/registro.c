#define _GNU_SOURCE
#include <security/pam_modules.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

PAM_EXTERN int pam_sm_open_session(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
    (void)pamh; (void)flags; (void)argc; (void)argv;
    const char *marca = getenv("CONTROLE_TEST_MARK");
    if (marca) { FILE *m = fopen(marca,"w"); if (m) fclose(m); }
    const char *espera = getenv("CONTROLE_TEST_DELAY");
    if (espera) usleep((useconds_t)atoi(espera)*1000);
    if (getenv("CONTROLE_TEST_NO_REGISTER")) return PAM_SUCCESS;
    FILE *f = fopen(getenv("CONTROLE_TEST_STATE"), "a");
    if (!f) return PAM_SESSION_ERR;
    fprintf(f, "%s %d 0\n", getenv("CONTROLE_TEST_UID"), getpid());
    fclose(f);
    return PAM_SUCCESS;
}
PAM_EXTERN int pam_sm_close_session(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
    (void)pamh; (void)flags; (void)argc; (void)argv;
    /* Simula remoção pelo logind, conservando as demais sessões. */
    const char *path = getenv("CONTROLE_TEST_STATE");
    char temporario[4096];
    snprintf(temporario, sizeof(temporario), "%s.tmp", path);
    FILE *in = fopen(path, "r"), *out = fopen(temporario, "w");
    if (!in || !out) abort();
    unsigned uid; int pid, atual;
    while (fscanf(in, "%u %d %d", &uid, &pid, &atual) == 3)
        if (pid != getpid()) fprintf(out, "%u %d %d\n", uid, pid, atual);
    fclose(in); fclose(out);
    return rename(temporario, path) ? PAM_SESSION_ERR : PAM_SUCCESS;
}
