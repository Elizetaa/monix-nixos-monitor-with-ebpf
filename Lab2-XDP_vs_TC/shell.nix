{ pkgs ? import <nixpkgs> {} }:

let
  linuxHeaders = pkgs.linuxHeaders;
in
pkgs.mkShell {
  packages = with pkgs; [
    llvmPackages.clang-unwrapped
    llvm
    bpftools
    clang
    gcc
    gnumake
    iproute2
    libbpf
    linuxHeaders
    pkg-config
    tcpdump
    netcat-openbsd
  ];

  # Os headers UAPI (incluindo linux/types.h) ficam em include/uapi no NixOS.
  NIX_CFLAGS_COMPILE = "-I${linuxHeaders}/include/uapi -I${linuxHeaders}/include";
}
