# Habilitar o BPF LSM

Este laboratório possui duas partes. O Kprobe funciona sem o BPF LSM, mas o
experimento que bloqueia a execução do `curl` exige que o LSM `bpf` esteja
ativo desde a inicialização do kernel.

Na seção sobre WSL 2, o procedimento alterna entre dois ambientes:

- **PowerShell do Windows:** configura e reinicia o WSL com comandos como
  `wsl --shutdown`.
- **terminal WSL:** executa os comandos Linux dentro da distribuição do WSL,
  como `sudo`, `mount` e `cat`;


Observe o ambiente indicado no título de cada etapa antes de executar os
comandos.

## Sumário

- [WSL 2](#wsl-2)
  1. [Terminal WSL: verifique os LSMs ativos](#1-terminal-wsl-verifique-os-lsms-ativos)
  2. [Terminal WSL: verifique o suporte do kernel](#2-terminal-wsl-verifique-o-suporte-do-kernel)
  3. [PowerShell do Windows: configure o BPF LSM](#3-powershell-do-windows-configure-o-bpf-lsm)
  4. [PowerShell do Windows: reinicie o WSL](#4-powershell-do-windows-reinicie-o-wsl)
  5. [Terminal WSL: confirme a ativação](#5-terminal-wsl-confirme-a-ativação)
- [Ubuntu instalado diretamente](#ubuntu-instalado-diretamente)
  1. [Verifique o suporte do kernel](#1-verifique-o-suporte-do-kernel)
  2. [Identifique os LSMs ativos](#2-identifique-os-lsms-ativos)
  3. [Adicione o parâmetro ao GRUB](#3-adicione-o-parâmetro-ao-grub)
  4. [Reinicie e confirme a ativação](#4-reinicie-e-confirme-a-ativação)

## WSL 2

### 1. Terminal WSL: verifique os LSMs ativos

Abra o **terminal WSL**. Primeiro, confirme que o `securityfs` está montado:

```bash
# Cria o diretório que será usado como ponto de montagem, caso ele não exista.
sudo mkdir -p /sys/kernel/security

# Verifica silenciosamente se o securityfs já está montado.
# Se não estiver, || executa o comando de montagem da linha seguinte.
mountpoint -q /sys/kernel/security || \
sudo mount -t securityfs securityfs /sys/kernel/security
```

O `securityfs` disponibiliza interfaces de segurança do kernel. Consulte os
Linux Security Modules ativos:

```bash
# Exibe a lista de LSMs que estão ativos no kernel em execução.
sudo cat /sys/kernel/security/lsm
```

Se a saída contiver `bpf`, nenhuma alteração é necessária. Continue em
[Retorne ao repositório](#retorne-ao-repositório).

Se `bpf` não aparecer, siga as próximas etapas.

### 2. Terminal WSL: verifique o suporte do kernel

Ainda no **terminal WSL**, confirme se o kernel foi compilado com suporte ao
BPF LSM:

```bash
# Procura CONFIG_BPF_LSM na configuração compactada exposta pelo kernel.
# Se esse arquivo não existir, || consulta a configuração da versão em execução.
# uname -r retorna exatamente a versão atual do kernel.
zgrep '^CONFIG_BPF_LSM=' /proc/config.gz 2>/dev/null || \
grep '^CONFIG_BPF_LSM=' /boot/config-"$(uname -r)" 2>/dev/null
```

O redirecionamento `2>/dev/null` oculta apenas a mensagem de erro causada pela
ausência de um dos arquivos. O resultado esperado é:

```text
CONFIG_BPF_LSM=y
```

Esse resultado indica que o suporte foi incluído durante a compilação do
kernel. Se o resultado for `# CONFIG_BPF_LSM is not set`, o kernel atual não
oferece o recurso. Atualize o WSL e o kernel antes de continuar:

```powershell
# Execute estes comandos no PowerShell do Windows, não no terminal WSL.
# Baixa e instala a atualização mais recente disponível para o WSL.
wsl --update

# Encerra todas as distribuições e o kernel do WSL para aplicar a atualização.
wsl --shutdown
```

Abra novamente o terminal WSL e repita as etapas 1 e 2. Se uma versão
atualizada continuar mostrando que `CONFIG_BPF_LSM` não está definido, será
necessário usar um kernel do WSL com esse suporte ou um kernel personalizado.

### 3. PowerShell do Windows: configure o BPF LSM

> Esta configuração é realizada no Windows e se aplica globalmente às
> distribuições executadas com WSL 2.

Feche trabalhos em execução no terminal WSL. Abra o **PowerShell do Windows** e
execute:

```powershell
# Abre no Bloco de Notas o .wslconfig do perfil do usuário atual do Windows.
notepad $env:USERPROFILE\.wslconfig
```

Se o arquivo não existir, o Bloco de Notas perguntará se deseja criá-lo.
Adicione:

```ini
[wsl2]
kernelCommandLine=lsm=landlock,lockdown,yama,integrity,apparmor,bpf
```

A seção `[wsl2]` reúne as configurações globais do WSL 2. A propriedade
`kernelCommandLine` acrescenta parâmetros à inicialização do kernel, enquanto
`lsm=` determina quais módulos de segurança serão ativados.

Se o arquivo já possuir uma seção `[wsl2]`, não crie outra: adicione somente
a propriedade `kernelCommandLine` dentro da seção existente.

Se já existir uma propriedade `kernelCommandLine`, preserve os outros
parâmetros. Caso ela ainda não possua `lsm=`, acrescente o parâmetro na mesma
linha, separado por espaço. Caso já possua `lsm=`, não crie um segundo:
acrescente `bpf` à lista existente.

> Não utilize apenas `lsm=bpf`, pois isso pode retirar outros mecanismos de
> segurança da lista.

Salve e feche o arquivo.

### 4. PowerShell do Windows: reinicie o WSL

Ainda no **PowerShell do Windows**, execute:

```powershell
# Encerra todas as distribuições e o kernel atual do WSL.
# A nova configuração será aplicada quando o Ubuntu for aberto novamente.
wsl --shutdown
```

Depois que o comando terminar, abra novamente o terminal WSL pelo menu Iniciar
do Windows ou execute `wsl` no PowerShell:

```powershell
# Inicia novamente a distribuição Linux padrão do WSL.
wsl
```

### 5. Terminal WSL: confirme a ativação

De volta ao **terminal WSL**, verifique se o parâmetro foi recebido pelo
kernel:

```bash
# Exibe os parâmetros usados para inicializar o kernel atual.
cat /proc/cmdline
```

A saída deve conter:

```text
lsm=landlock,lockdown,yama,integrity,apparmor,bpf
```

O comando `wsl --shutdown` encerra o kernel, e montagens realizadas manualmente
podem não permanecer depois que o WSL for iniciado novamente. Por isso,
verifique o `securityfs` outra vez:

```bash
# Garante que o diretório usado como ponto de montagem exista.
sudo mkdir -p /sys/kernel/security

# Verifica se o securityfs já está montado.
if mountpoint -q /sys/kernel/security; then
    # Informa quando o filesystem já está disponível.
    echo "securityfs já está montado."
else
    # Monta o securityfs somente quando ele ainda não está disponível.
    sudo mount -t securityfs securityfs /sys/kernel/security
    echo "securityfs foi montado."
fi
```

Confira novamente os LSMs ativos:

```bash
# Exibe a lista de LSMs efetivamente ativos após a reinicialização.
sudo cat /sys/kernel/security/lsm
```

A lista deve conter `bpf`. Por exemplo:

```text
capability,landlock,lockdown,yama,integrity,apparmor,bpf
```

O módulo `capability` pode aparecer automaticamente mesmo sem estar escrito no
parâmetro `lsm=`. Quando `bpf` aparecer na lista, a ativação estará concluída e
as duas partes do laboratório poderão ser executadas.

### Retorne ao repositório

Uma nova sessão do WSL normalmente começa no diretório inicial do usuário,
e não na pasta em que o laboratório foi clonado. No **terminal WSL**,
execute:

```bash
# Entra na raiz do repositório clonado no diretório inicial do usuário.
cd ~/ebpf-grad-labs

# Exibe o caminho atual para confirmar a localização.
pwd

# Lista o conteúdo; a saída deve incluir o diretório Lab1-Tipos_eBPF.
ls
```

Se o repositório foi clonado em outro local, substitua o caminho usado no
comando `cd`.

### Restaurar a configuração anterior no WSL 2

No **PowerShell do Windows**, abra novamente o arquivo:

```powershell
# Abre o arquivo global de configuração do WSL 2.
notepad $env:USERPROFILE\.wslconfig
```

Remova somente o parâmetro `lsm=...` adicionado por este laboratório e
preserve as demais configurações. Salve o arquivo e execute:

```powershell
# Reinicia completamente o WSL para aplicar a configuração restaurada.
wsl --shutdown
```

## Ubuntu instalado diretamente

> O ambiente principal deste laboratório é Ubuntu sobre WSL 2. Esta seção é
> uma alternativa para instalações convencionais do Ubuntu Desktop ou Server
> que utilizam o GRUB. Ubuntu Core, Raspberry Pi e sistemas com outro
> bootloader podem exigir procedimentos diferentes.

Em uma instalação convencional do Ubuntu, os parâmetros de inicialização são
configurados no GRUB, e não no arquivo `.wslconfig` do Windows. Todos os
comandos desta seção devem ser executados no **terminal Ubuntu**.

### 1. Verifique o suporte do kernel

```bash
# Procura CONFIG_BPF_LSM na configuração compactada exposta pelo kernel.
# Se ela não existir, consulta o arquivo correspondente à versão em execução.
zgrep '^CONFIG_BPF_LSM=' /proc/config.gz 2>/dev/null || \
grep '^CONFIG_BPF_LSM=' /boot/config-"$(uname -r)" 2>/dev/null
```

O resultado esperado é `CONFIG_BPF_LSM=y`. Se aparecer
`# CONFIG_BPF_LSM is not set`, atualize o kernel por um mecanismo compatível
com a versão do Ubuntu utilizada antes de continuar.

### 2. Identifique os LSMs ativos

```bash
# Garante que o diretório usado como ponto de montagem exista.
sudo mkdir -p /sys/kernel/security

# Monta o securityfs somente se ele ainda não estiver montado.
if mountpoint -q /sys/kernel/security; then
    # Informa que nenhuma nova montagem foi necessária.
    echo "securityfs já está montado."
else
    # Monta a interface de segurança do kernel no diretório preparado.
    sudo mount -t securityfs securityfs /sys/kernel/security

    # Confirma que a montagem foi realizada.
    echo "securityfs foi montado."
fi

# Exibe os LSMs ativos para que a lista atual seja preservada.
sudo cat /sys/kernel/security/lsm
```

Anote a lista exibida. O próximo passo deve preservar os LSMs existentes e
acrescentar `bpf`. Não copie uma lista de outra máquina, pois os mecanismos
disponíveis podem variar entre kernels e distribuições.

Por exemplo, se a saída for:

```text
capability,landlock,lockdown,yama,integrity,apparmor
```

use no parâmetro de inicialização:

```text
lsm=landlock,lockdown,yama,integrity,apparmor,bpf
```

O LSM `capability` não precisa ser incluído no parâmetro `lsm=`, pois o kernel
o ativa automaticamente.

### 3. Adicione o parâmetro ao GRUB

Crie um arquivo adicional de configuração, sem alterar diretamente
`/etc/default/grub`:

```bash
# Abre no Nano um arquivo adicional de configuração do GRUB.
sudo nano /etc/default/grub.d/99-bpf-lsm.cfg
```

Adicione uma única linha. Substitua a lista do exemplo pelos LSMs encontrados
na etapa anterior, preserve a ordem deles e mantenha `bpf` ao final:

```text
GRUB_CMDLINE_LINUX="${GRUB_CMDLINE_LINUX} lsm=landlock,lockdown,yama,integrity,apparmor,bpf"
```

No Nano, pressione `Ctrl+O`, `Enter` e `Ctrl+X` para salvar e sair. Depois,
execute:

```bash
# Regera a configuração de inicialização do GRUB com o novo parâmetro.
sudo update-grub
```

O comando deve terminar sem erros. A alteração somente terá efeito depois da
reinicialização.

### 4. Reinicie e confirme a ativação

```bash
# Reinicia o Ubuntu para que o kernel receba o novo parâmetro.
sudo reboot
```

Depois que o sistema iniciar novamente, execute no **terminal Ubuntu**:

```bash
# Confirma que o parâmetro lsm= foi recebido pelo kernel.
cat /proc/cmdline

# Confirma que o BPF LSM está efetivamente ativo.
sudo cat /sys/kernel/security/lsm
```

A primeira saída deve conter a lista `lsm=...` configurada anteriormente. A
segunda deve preservar os mecanismos anteriores e conter `bpf`. Quando isso
acontecer, retorne à raiz do repositório e execute `make setup` e `make attach`
conforme as instruções do [README do Lab 1](README.md#executar-o-laboratório).

### Restaurar a configuração anterior no Ubuntu

```bash
# Remove somente o arquivo adicional criado por este guia.
sudo rm /etc/default/grub.d/99-bpf-lsm.cfg

# Regera a configuração do GRUB sem o parâmetro removido.
sudo update-grub

# Reinicia o Ubuntu para aplicar a configuração restaurada.
sudo reboot
```
