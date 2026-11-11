# VM para quem já tem Nix no host (não é preciso instalar NixOS):
#
#   nix run .#vm                      # na raiz do repositório
#
# Mesmos serviços de nixos/monix.nix. Portas encaminhadas para o host:
#   http://localhost:3000  Grafana (admin / admin)
#   http://localhost:9090  Prometheus
#   http://localhost:9108  Monix (/metrics e /api/metrics)
#   ssh -p 2222 monix@localhost  (senha: monix)
#
# O diretório em que você rodou `nix run` aparece dentro da VM em /mnt/monix.
{ modulesPath, ... }:

{
  imports = [ (modulesPath + "/virtualisation/qemu-vm.nix") ./monix.nix ];

  system.stateVersion = "26.05";
  networking.hostName = "monix-vm";

  virtualisation = {
    memorySize = 4096; # MiB
    cores = 4;
    diskSize = 8192; # MiB
    graphics = false; # console no próprio terminal (sair: Ctrl+A, X)
    forwardPorts = [
      { from = "host"; host.port = 3000; guest.port = 3000; }
      { from = "host"; host.port = 9090; guest.port = 9090; }
      { from = "host"; host.port = 9108; guest.port = 9108; }
      { from = "host"; host.port = 2222; guest.port = 22; }
    ];
    sharedDirectories.monix = {
      source = ''"$PWD"'';
      target = "/mnt/monix";
    };
  };

  users.users.monix = {
    isNormalUser = true;
    extraGroups = [ "wheel" ];
    initialPassword = "monix";
  };
  security.sudo.wheelNeedsPassword = false;
  services.getty.autologinUser = "monix";
  services.openssh = {
    enable = true;
    settings.PasswordAuthentication = true;
  };
  networking.firewall.allowedTCPPorts = [ 22 ];
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
}
