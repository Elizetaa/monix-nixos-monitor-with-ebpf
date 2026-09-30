# shell.nix - ambiente de desenvolvimento para o projeto cpu_alert (eBPF)
#
# Uso:
#   nix-shell          # entra no ambiente com clang, libbpf, make, etc.
#   make               # compila dentro dele
#   make run           # executa (usa sudo se você não for root)

{ pkgs ? import <nixpkgs> { } }:

pkgs.mkShell {
  packages = with pkgs; [
    gnumake
    gcc
    clang        # compila o lado kernel (C -> bytecode BPF)
    pkg-config   # o Makefile usa para achar a libbpf
    libbpf
    elfutils     # dependências da libbpf
    zlib
  ];

  # O wrapper de clang/gcc do Nix liga flags de hardening por padrão
  # (zerocallusedregs, stackprotector, fortify...). O backend BPF não
  # suporta várias delas e a compilação falha. Desligamos todas aqui.
  hardeningDisable = [ "all" ];

  # No NixOS não existe /usr/include, então os headers do kernel
  # (linux/bpf.h, asm/types.h) vêm do pacote linuxHeaders. O Makefile lê
  # esta variável de ambiente e a passa ao clang.
  BPF_EXTRA_CFLAGS = "-I${pkgs.linuxHeaders}/include";
}
