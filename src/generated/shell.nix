{ pkgs ? import <nixpkgs> { }, observability ? false }:
pkgs.mkShell {
  packages = with pkgs; [
    python3
    gnumake
    gcc
    llvmPackages.clang-unwrapped
    bpftools
    pkg-config
    libbpf
    elfutils
    zlib
  ] ++ pkgs.lib.optionals observability (with pkgs; [
    prometheus
    prometheus.cli
    grafana
  ]);
  # hardeningDisable = [ "all" ];
  # O backend BPF não aceita flags de hardening dos wrappers do Nix.
  CLANG = "${pkgs.llvmPackages.clang-unwrapped}/bin/clang";
}
