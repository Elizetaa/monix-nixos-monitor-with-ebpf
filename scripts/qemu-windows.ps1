<#
  VM NixOS do Monix no QEMU para Windows 11.

  Primeira vez (baixa a ISO, cria o disco e boota o instalador):
    powershell -ExecutionPolicy Bypass -File scripts\qemu-windows.ps1 -Instalar
  Depois de instalado:
    powershell -ExecutionPolicy Bypass -File scripts\qemu-windows.ps1

  Portas no Windows: Grafana http://localhost:3000, Prometheus :9090,
  Monix :9108, SSH: ssh -p 2222 monix@localhost
#>
param(
  [switch]$Instalar,
  [ValidateSet("whpx", "tcg")][string]$Accel = "tcg",
  [int]$MemoriaMB = 8192,
  [int]$Cpus = 8,
  [string]$QemuDir = "C:\Program Files\qemu",
  [string]$VmDir = (Join-Path $PSScriptRoot "..\vm")
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue" # deixa o download muito mais rapido

$qemu = Join-Path $QemuDir "qemu-system-x86_64.exe"
$qemuImg = Join-Path $QemuDir "qemu-img.exe"
if (-not (Test-Path $qemu)) { throw "QEMU nao encontrado em $QemuDir (use -QemuDir)" }

New-Item -ItemType Directory -Force $VmDir | Out-Null
$disco = Join-Path $VmDir "nixos.qcow2"
$iso = Join-Path $VmDir "nixos-minimal-26.05.iso"

if (-not (Test-Path $disco)) {
  & $qemuImg create -f qcow2 $disco 40G
}
if ($Instalar -and -not (Test-Path $iso)) {
  Write-Host "Baixando a ISO minima do NixOS 26.05 (~1 GB)..."
  Invoke-WebRequest "https://channels.nixos.org/nixos-26.05/latest-nixos-minimal-x86_64-linux.iso" -OutFile $iso
}

# tcg (padrao) = emulacao pura: funciona sem mexer no Windows, mas e lenta.
# whpx = aceleracao do Hyper-V (exige SVM na BIOS + Plataforma do Hipervisor).
$acelerador = if ($Accel -eq "whpx") { "whpx,kernel-irqchip=off" } else { "tcg,thread=multi,tb-size=512" }
# "max": em tcg emula todos os recursos de CPU que o QEMU conhece (BPF JIT, AVX etc.).
$cpuModel = "max"
$portas = "hostfwd=tcp:127.0.0.1:3000-:3000,hostfwd=tcp:127.0.0.1:9090-:9090," +
          "hostfwd=tcp:127.0.0.1:9108-:9108,hostfwd=tcp:127.0.0.1:2222-:22"
$qemuArgs = @(
  "-accel", $acelerador,
  "-cpu", $cpuModel,
  "-m", $MemoriaMB,
  "-smp", $Cpus,
  "-drive", "file=$disco,if=virtio,format=qcow2",
  "-nic", "user,model=virtio-net-pci,$portas",
  "-vga", "std"
)
if ($Instalar) { $qemuArgs += @("-cdrom", $iso, "-boot", "d") }

Write-Host "$qemu $($qemuArgs -join ' ')"
& $qemu @qemuArgs
