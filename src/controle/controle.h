#ifndef CONTROLE_H
#define CONTROLE_H
#include <sys/types.h>
#include <stddef.h>

struct sessao { uid_t uid; pid_t lider; int atual; };
struct sessoes { struct sessao *itens; size_t total; };

/* -1: verificação impossível; 0: comum; 1: administrador. */
int politica_admin(uid_t uid, const char *grupo);
int politica_permitir(uid_t uid, const char *grupo, const struct sessoes *s);
int sessoes_ler(struct sessoes *s);
int sessoes_travar(void); /* fd ou errno negativo; fechar libera o flock. */
#endif
