# configuration.nix completo da VM do projeto (NixOS 26.05 no QEMU).
#
# Reúne tudo o que o repositório oferece ao NixOS:
#   - telemetria eBPF (Monix) + Prometheus + Grafana  -> nixos/monix.nix
#   - controle de acesso via PAM                       -> src/controle/modulo.nix
#
# Uso: o repositório fica clonado em /etc/nixos/monix e este arquivo é copiado
# para /etc/nixos/configuration.nix (ver documentation/vm-windows.md).
#
# Depois de instalada, para aplicar mudanças do repositório:
#   sudo git -C /etc/nixos/monix pull && sudo nixos-rebuild switch
{ ... }:

{
  imports = [
    ./hardware-configuration.nix # gerado por nixos-generate-config
    ./monix/nixos/monix.nix      # Monix + Prometheus + Grafana
    ./monix/src/controle/modulo.nix # controle de acesso (PAM)
  ];

  # Um UID comum por vez; root e o grupo wheel (usuário monix) são exceções.
  services.controleAcesso = {
    enable = true;
    grupoAdministrativo = "wheel";
    servicos = [ "sshd" "login" "su" "sudo" ];
  };

  # Disco virtio com tabela MBR e GRUB (modo BIOS, padrão do QEMU).
  boot.loader.grub.enable = true;
  boot.loader.grub.device = "/dev/vda";

  networking.hostName = "monix-vm";
  time.timeZone = "America/Sao_Paulo";
  i18n.defaultLocale = "pt_BR.UTF-8";
  console.keyMap = "br-abnt2";

  users.users.monix = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    initialPassword = "monix"; # troque com `passwd` depois do primeiro login
  };
  security.sudo.wheelNeedsPassword = false;

  services.openssh = {
    enable = true;
    settings.PasswordAuthentication = true;
  };
  networking.firewall.allowedTCPPorts = [ 22 ];

  # Agente do QEMU (desligamento limpo) e flakes para `nix develop` no projeto.
  services.qemuGuest.enable = true;
  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  system.stateVersion = "26.05";
}
