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
  ];

  NIX_CFLAGS_COMPILE = "-I${linuxHeaders}/include";
}
