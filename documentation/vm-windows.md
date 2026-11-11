# VM NixOS no Windows 11 (QEMU) com Monix + Grafana

Resultado: uma VM NixOS 26.05 (kernel 6.18 com BTF) rodando o Monix como
serviço, o Prometheus coletando `/metrics` e o Grafana com o dashboard pronto,
acessível pelo navegador do Windows em http://localhost:3000.

Não é preciso instalar Nix no Windows: o Nix vem junto com o NixOS dentro da VM.

## 0. Pré-requisitos no Windows

1. QEMU para Windows instalado (padrão: `C:\Program Files\qemu`).
2. *(Opcional, deixa a VM muito mais rápida)* ligue o SVM/VT-x na BIOS, ative a
   **Plataforma do Hipervisor do Windows** (PowerShell como administrador),
   reinicie e use `-Accel whpx` no script. Sem isso o script usa `tcg` (lento):
   ```powershell
   Enable-WindowsOptionalFeature -Online -FeatureName HypervisorPlatform -All
   ```
   A virtualização (VT-x/AMD-V) precisa estar ligada na BIOS/UEFI.
3. O repositório no GitHub precisa ter `nixos/`, `config/grafana/dashboards/`
   e os headers `src/telemetria/coletores/ebpf/*.bpf.h` (a VM clona de lá).

## 1. Boot do instalador

Na raiz do repositório, no PowerShell:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\qemu-windows.ps1 -Instalar
```
O script baixa a ISO mínima para `vm\`, cria um disco de 40 GB e abre a janela da VM.
Padrão: 8 GB de RAM, 8 CPUs, modo `tcg`. Ajuste com `-MemoriaMB`, `-Cpus`, `-VmDir D:\monix-vm`.

## 2. Instalação (dentro da janela da VM)

```sh
sudo loadkeys br-abnt2          # se o teclado for ABNT2
sudo -i
parted /dev/vda -- mklabel msdos
parted /dev/vda -- mkpart primary ext4 1MB 100%
mkfs.ext4 -L nixos /dev/vda1
mount /dev/disk/by-label/nixos /mnt
nixos-generate-config --root /mnt
nix-shell -p git --run "git clone https://github.com/Elizetaa/monix-nixos-monitor-with-ebpf /mnt/etc/nixos/monix"
cp /mnt/etc/nixos/monix/nixos/configuration.nix /mnt/etc/nixos/configuration.nix
nixos-install                   # demora; no fim pede a senha do root
poweroff
```

## 3. Uso no dia a dia

```powershell
powershell -ExecutionPolicy Bypass -File scripts\qemu-windows.ps1
```
Login `monix` / senha `monix`. Ou pelo Windows: `ssh -p 2222 monix@localhost`.

No primeiro boot o serviço compila os objetos eBPF (1–2 minutos). Conferir:
```sh
systemctl status monix
journalctl -u monix -f
curl -s localhost:9108/metrics | head -40
curl -s localhost:9108/api/metrics | jq .cpu
```
No Windows: Grafana em http://localhost:3000 (admin/admin) → dashboard
**Monix — monitor eBPF**. Prometheus em http://localhost:9090 → Status → Targets.

## 4. Testes do projeto na VM

```sh
git clone https://github.com/Elizetaa/monix-nixos-monitor-with-ebpf ~/monix
cd ~/monix
nix develop .#telemetria
make -C src/telemetria check-native build test
sudo "$(command -v python3)" src/telemetria/tests/validar_host.py --require-ebpf
```
Para rodar a versão de `~/monix` manualmente, pare o serviço antes
(`sudo systemctl stop monix`), porque ele usa a porta 9108.

## 5. Atualizar a VM depois de um push

```sh
sudo git -C /etc/nixos/monix pull
sudo nixos-rebuild switch
```

## Problemas comuns

| Sintoma | O que fazer |
| --- | --- |
| `WHPX: No accelerator found` | Passo 0.2 + reiniciar; ou `-Accel tcg` |
| Porta 3000/9090 ocupada no Windows | Feche o outro programa ou mude o `hostfwd` no script |
| `monix` falha em `make` | `journalctl -u monix`; verifique se os `*.bpf.h` estão no GitHub |
| Painéis de temperatura/GPU vazios | Normal em VM; a tabela de estado mostra o motivo |
