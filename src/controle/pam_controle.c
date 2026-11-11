#define _GNU_SOURCE
#include "controle.h"
#include <pwd.h>
#include <security/pam_modules.h>
#include <security/pam_ext.h>
#include <stdlib.h>
#include <string.h>
#include <syslog.h>
#include <unistd.h>

struct transacao { int fd; uid_t uid; };
static const char chave[] = "controle.transacao";

static void liberar(pam_handle_t *pamh, void *dados, int status)
{
    (void)pamh; (void)status;
    struct transacao *t = dados;
    /* pam_end em sucesso/erro e morte do processo desfazem a reserva provisória. */
    if (t->fd >= 0) close(t->fd);
    free(t);
}

static int negar(pam_handle_t *pamh, const char *motivo)
{
    pam_syslog(pamh, LOG_ERR, "controle: %s", motivo);
    pam_error(pamh, "Controle de acesso: %s", motivo);
    /* sudo trata PAM_SESSION_ERR como não fatal; a política deve impedir execução. */
    return PAM_PERM_DENIED;
}

PAM_EXTERN int pam_sm_open_session(pam_handle_t *pamh, int flags,
                                   int argc, const char **argv)
{
    (void)flags;
    const char *grupo = "wheel", *usuario;
    int confirmar = 0;
    for (int i = 0; i < argc; i++) {
        if (!strcmp(argv[i], "confirmar")) confirmar = 1;
        else if (!strcmp(argv[i], "reservar")) confirmar = 0;
        else if (!strncmp(argv[i], "grupo=", 6) && argv[i][6]) grupo = argv[i] + 6;
        else return negar(pamh, "opção inválida na configuração PAM");
    }
    if (pam_get_user(pamh, &usuario, NULL) != PAM_SUCCESS || !usuario)
        return negar(pamh, "PAM_USER ausente");
    char buffer[16384];
    struct passwd conta, *pw;
    if (getpwnam_r(usuario, &conta, buffer, sizeof(buffer), &pw) || !pw)
        return negar(pamh, "não foi possível resolver PAM_USER");
    uid_t uid = pw->pw_uid;
    int admin = politica_admin(uid, grupo);
    if (admin == 1) return PAM_SUCCESS; /* Só esta regra é dispensada; nunca sufficient. */
    if (admin < 0) return negar(pamh, "não foi possível verificar o grupo administrativo");
    const void *dados = NULL;
    pam_get_data(pamh, chave, &dados);
    struct transacao *t = (struct transacao *)dados;
    if (!confirmar) {
        if (t && t->fd >= 0) return negar(pamh, "reserva duplicada; verifique includes da pilha PAM");
        t = malloc(sizeof(*t));
        if (!t) return PAM_BUF_ERR;
        t->uid = uid;
        t->fd = sessoes_travar();
        if (t->fd < 0) {
            pam_syslog(pamh, LOG_ERR, "controle: lock: %s", strerror(-t->fd));
            free(t);
            return negar(pamh, "estado protegido indisponível ou admissão ocupada");
        }
        if (pam_set_data(pamh, chave, t, liberar) != PAM_SUCCESS) {
            liberar(pamh, t, 0);
            return PAM_SYSTEM_ERR;
        }
    } else if (!t || t->fd < 0 || t->uid != uid) {
        return negar(pamh, "confirmação sem reserva válida para este UID");
    }
    struct sessoes s;
    int r = sessoes_ler(&s);
    if (r < 0) pam_syslog(pamh, LOG_ERR, "controle: logind: %s", strerror(-r));
    int permitir = r < 0 ? -1 : politica_permitir(uid, grupo, &s);
    int registrada = 0;
    for (size_t i = 0; i < s.total; i++)
        if (s.itens[i].uid == uid &&
            (s.itens[i].lider == getpid() || s.itens[i].atual)) registrada = 1;
    free(s.itens);
    if (permitir != 1 || (confirmar && !registrada)) {
        close(t->fd); t->fd = -1;
        if (permitir < 0) return negar(pamh, "falha ao consultar logind ou identidades das sessões");
        if (!permitir) return negar(pamh, "outro UID comum possui sessão aberta");
        return negar(pamh, "pam_systemd não registrou sessão deste UID para o processo");
    }
    /* Não liberar entre verificar e registrar. A segunda chamada sucede pam_systemd. */
    if (confirmar) { close(t->fd); t->fd = -1; }
    return PAM_SUCCESS;
}

PAM_EXTERN int pam_sm_close_session(pam_handle_t *pamh, int flags,
                                    int argc, const char **argv)
{
    (void)flags; (void)argc; (void)argv;
    const void *dados = NULL;
    if (pam_get_data(pamh, chave, &dados) == PAM_SUCCESS) {
        struct transacao *t = (struct transacao *)dados;
        if (t->fd >= 0) { close(t->fd); t->fd = -1; }
    }
    /* O logind acompanha cada sessão, inclusive locks de tela e escopos closing. */
    return PAM_SUCCESS;
}
