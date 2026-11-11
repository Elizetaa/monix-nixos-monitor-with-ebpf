#define _GNU_SOURCE
#include <security/pam_appl.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

/* O sandbox não fornece NETLINK_AUDIT. Só o cliente de teste desativa auditoria. */
int audit_open(void) { errno = EPROTONOSUPPORT; return -1; }

static int conversa(int n, const struct pam_message **m, struct pam_response **r, void *p)
{
    (void)p;
    for (int i=0; i<n; i++) fprintf(stderr, "%s\n", m[i]->msg);
    *r = calloc((size_t)n, sizeof(**r));
    return *r ? PAM_SUCCESS : PAM_BUF_ERR;
}
int main(int argc, char **argv)
{
    if (argc != 4) return 2;
    pam_handle_t *pamh = NULL;
    struct pam_conv conv = {.conv=conversa};
    int r = pam_start_confdir(argv[2], argv[3], &conv, argv[1], &pamh);
    if (r != PAM_SUCCESS) return 3;
    r = pam_open_session(pamh, 0);
    printf("%d\n", r); fflush(stdout);
    int comando = getchar();
    if (comando == 'x') _exit(0);
    if (comando == 'c') pam_close_session(pamh, 0);
    pam_end(pamh, r);
    return 0;
}
