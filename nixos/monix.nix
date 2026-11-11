# Módulo do Monix para qualquer NixOS (VM instalada no QEMU, VirtualBox ou máquina real).
#
# Em /etc/nixos/configuration.nix:
#   imports = [ ./hardware-configuration.nix /home/monix/monix/nixos/monix.nix ];
# e depois: sudo nixos-rebuild switch
#
# Sobe: kernel 6.18 com BTF + Monix (systemd, porta 9108) + Prometheus (9090)
# + Grafana (3000, admin/admin) com o dashboard já provisionado.
{ config, lib, pkgs, ... }:

let
  monixPort = 9108;

  # Cópia do código no Nix store (somente arquivos versionados pelo git).
  telemetriaSrc = ../src/telemetria;

  # Ferramentas que o Makefile de src/telemetria precisa (as mesmas do shell.nix).
  buildTools = with pkgs; [ gnumake gcc llvmPackages.clang-unwrapped bpftools pkg-config coreutils ];
  pkgConfigPath = lib.makeSearchPathOutput "dev" "lib/pkgconfig" (with pkgs; [ libbpf elfutils zlib ]);
in
{
  # O projeto mira Linux 6.18 (LTS). Se o canal não tiver esse attr, usa o mais recente.
  boot.kernelPackages = pkgs.linuxPackages_6_18 or pkgs.linuxPackages_latest;

  environment.systemPackages = buildTools ++ (with pkgs; [ git curl jq python3 bpftrace ]);
  networking.firewall.allowedTCPPorts = [ 3000 9090 monixPort ];

  # ---------------------------------------------------------------- Monix
  # O vmlinux.h precisa vir do BTF do kernel em execução
  # (/sys/kernel/btf/vmlinux), que não existe dentro do sandbox do Nix.
  # Por isso o build dos objetos eBPF roda no boot (ExecStartPre), em /var/lib/monix.
  systemd.services.monix = {
    description = "Monix: telemetria eBPF exportada para Prometheus";
    wantedBy = [ "multi-user.target" ];
    after = [ "network.target" ];
    path = buildTools;
    environment = {
      CLANG = "${pkgs.llvmPackages.clang-unwrapped}/bin/clang";
      PKG_CONFIG_PATH = pkgConfigPath;
      PYTHONPATH = "/var/lib/monix/src";
      PYTHONDONTWRITEBYTECODE = "1";
    };
    preStart = ''
      rm -rf /var/lib/monix/src
      mkdir -p /var/lib/monix/src
      cp -r ${telemetriaSrc} /var/lib/monix/src/telemetria
      chmod -R u+w /var/lib/monix/src
      make -C /var/lib/monix/src/telemetria build
    '';
    serviceConfig = {
      # eBPF exige root (ou CAP_BPF + CAP_PERFMON + CAP_SYS_RESOURCE).
      User = "root";
      StateDirectory = "monix";
      WorkingDirectory = "/var/lib/monix";
      ExecStart = "${pkgs.python3}/bin/python3 -m telemetria --interval 2 --host 0.0.0.0 --port ${toString monixPort}";
      Restart = "on-failure";
      RestartSec = 5;
      TimeoutStartSec = 300; # primeira compilação
    };
  };

  # ---------------------------------------------------------------- Prometheus
  services.prometheus = {
    enable = true;
    port = 9090;
    globalConfig = {
      scrape_interval = "5s";
      evaluation_interval = "5s";
    };
    scrapeConfigs = [{
      job_name = "monix";
      metrics_path = "/metrics";
      static_configs = [{ targets = [ "127.0.0.1:${toString monixPort}" ]; }];
    }];
  };

  # ---------------------------------------------------------------- Grafana
  services.grafana = {
    enable = true;
    settings = {
      server = {
        http_addr = "0.0.0.0";
        http_port = 3000;
      };
      security = {
        admin_user = "admin";
        admin_password = "admin";
        # Somente para a VM de laboratório (fica legível no Nix store).
        secret_key = "monix-lab-vm-nao-use-em-producao";
      };
      analytics.reporting_enabled = false;
    };
    provision = {
      enable = true;
      datasources.settings.datasources = [{
        name = "Prometheus";
        uid = "prometheus";
        type = "prometheus";
        access = "proxy";
        url = "http://127.0.0.1:9090";
        isDefault = true;
      }];
      dashboards.settings.providers = [{
        name = "Monix";
        options.path = ../config/grafana/dashboards;
      }];
    };
  };
}
