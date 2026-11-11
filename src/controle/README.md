# Controle de acesso com PAM

Um UID comum por computador, com várias sessões desse UID. UID 0 e membros
do grupo administrativo são exceções. `PAM_USER` é resolvido para UID por NSS;
grupo primário e suplementares são considerados. Integração avaliada no NixOS 26.05.

## Arquitetura

- `pam_controle.c`: interface PAM e transação de abertura.
- `politica.c`: administradores e comparação dos UIDs.
- `sessoes.c`: `flock` entre processos e consulta ao logind por D-Bus.

A primeira regra `session requisite ... reservar` trava e verifica as sessões.
A última, `... confirmar`, exige que `pam_systemd` tenha registrado uma sessão
do UID para o processo, como líder ou integrante, e libera o lock. Não confia
em `XDG_SESSION_ID` fornecido pelo usuário. Verificar e registrar ficam na mesma
região crítica; não pode haver `sufficient`/salto que pule a confirmação.

Não há contador ou daemon novo: o logind acompanha as sessões. Logout parcial
e bloqueio de tela não liberam o UID. Sessões `closing` contam até desaparecer.
`pam_end`, inclusive após falha na pilha, e morte do processo liberam a reserva
provisória; eventual sessão logind em limpeza permanece ocupada. Reboot, PID
reutilizado ou arquivo antigo não deixam reservas próprias persistentes.

O estado próprio é somente `/run/controle-pam/admissao.lock`, root:root 0600,
em diretório 0700; symlinks e hardlinks são rejeitados. Não substituir o lock
em uso. Falhas de consulta/identidade/proteção negam comuns com mensagem PAM e
journal. Administradores identificados com segurança dispensam somente esta
política: as demais regras PAM continuam obrigatórias. Negações retornam
`PAM_PERM_DENIED`, pois sudo trata `PAM_SESSION_ERR` como não fatal.

## Build e testes

```sh
cd src/controle
nix-shell --run 'make test'
nix-instantiate --eval --strict --store dummy:// tests/nixos.nix
# Opcional: integração real em QEMU, com dependências maiores.
nix-build tests/vm.nix -o build/resultado-vm
```

`make` gera `build/pam_controle.so`; `make clean` remove `build/`. Dependências:
C, make, pkg-config, PAM, libsystemd, Python 3 e D-Bus. Testes locais dispensam
root, mas precisam criar socket Unix privado. Nix usa sua store; o link fica em
`build/`. Nenhum teste local modifica `/etc/pam.d`.

**Validado:** compilação com `-Wall -Wextra -Werror`; 20 testes com PAM real,
confdir privada, NSS/logind simulados e flock real entre processos; 5 testes do
leitor original em D-Bus privado. Cobrem exclusividade, aliases, múltiplas sessões,
admins, concorrência, abandono, SIGKILL, falha posterior, recuperação e proteção
do estado. Somente o cliente de teste desativa auditoria de kernel indisponível
no sandbox. A avaliação Nix verifica ordem, includes, preservação de
`auth/account/password`, serviços excluídos e desativação.
**Validado também em VM QEMU:** autologin SDDM, SSH do mesmo UID, exceções
administrativas, negação de outro UID via SSH/su/sudo e login getty em tty3.
Bob só entrou após o encerramento das sessões gráfica e TTY de Alice.

## NixOS

Importe `modulo.nix` e defina `services.controleAcesso.enable = true`, inicialmente
em uma VM; veja `exemplos/nixos.nix`. Aplique com `sudo nixos-rebuild switch`
somente no sistema escolhido. Nada é ativado automaticamente. Para desativar,
defina `enable = false` e reconstrua; nenhuma sessão existente é encerrada.

O grupo padrão é `wheel`. Para trocar, configure
`services.controleAcesso.grupoAdministrativo = "administradores"`, crie-o em
`users.groups` e associe contas via `users.users.<nome>.extraGroups` ou `group`.
Grupo ausente, erro NSS ou registro NSS maior que 16 KiB nega comuns;
UID 0 continua exceção quando resolvido com segurança.

Pilhas diretas padrão: `sshd`, `login`, `su`, `sudo`, com `startSession` habilitado.
**SDDM inclui login; sddm-autologin inclui SDDM** no NixOS avaliado: ambos são
cobertos sem duplicar regras; assertions conferem esses includes. Autologin TTY
passa por `login`. `kde` (desbloqueador), `sddm-greeter` e `systemd-user` não recebem
regras. Outro gerenciador exige revisar a pilha e ajustar `servicos` sem duplicar
reservas por includes.

## Limitações

A garantia cobre os serviços PAM configurados, não todos os processos. Sessões
interativas anteriores à ativação são lidas do logind. Dois UIDs já presentes
bloqueiam novas entradas comuns até o conflito desaparecer, sem expulsar ninguém.
Contam classes `user`/`user-*`; greeters, desbloqueadores, managers e tarefas
`background` são excluídos. Classe desconhecida ou corrida durante a consulta
nega conservadoramente; uma nova tentativa pode funcionar.

`su` e `sudo -u` consideram a **conta de destino**. Mesmo UID pode reutilizar a
sessão; outro UID comum é negado quando `pam_systemd` reutiliza a sessão original,
mesmo sem comuns conectados, pois falta uma sessão independente verificável.
Destinos administrativos seguem o restante do PAM. No NixOS avaliado, `su -l`
usa `su` e `sudo -i` usa `sudo`; se forem usados `su-l`/`sudo-i`, configure-os.
`runuser`, `run0`, contêineres e programas sem PAM não estão cobertos.

Aplicações precisam respeitar falhas de `pam_open_session` e chamar `pam_end`;
não devem manter descendentes com descritores herdados entre as duas regras.
Lock ocupado por aproximadamente 5 s nega a tentativa. Escopos logind presos
podem adiar a liberação: investigue com `loginctl`/journal, sem apagar estado
ou encerrar sessões automaticamente.

Referências: [pam_systemd](https://www.freedesktop.org/software/systemd/man/latest/pam_systemd.html),
[sd-login](https://www.freedesktop.org/software/systemd/man/latest/sd-login.html),
[PAM no NixOS](https://github.com/NixOS/nixpkgs/blob/nixos-26.05/nixos/modules/security/pam.nix).
