#include "../controle.h"
#include <stdio.h>
#include <stdlib.h>
int main(void)
{
    struct sessoes s;
    int r = sessoes_ler(&s);
    if (r < 0) return 1;
    printf("%zu", s.total);
    for (size_t i = 0; i < s.total; i++)
        printf(" %u:%d", (unsigned)s.itens[i].uid, s.itens[i].lider);
    puts("");
    free(s.itens);
    return 0;
}
