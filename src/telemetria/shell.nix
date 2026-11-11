{ pkgs ? import <nixpkgs> { } }:
pkgs.mkShell {
  packages = with pkgs; [ python3 gnumake gcc llvmPackages.clang-unwrapped
                         bpftools pkg-config libbpf elfutils zlib ];
  # Usa clang sem wrapper: flags de hardening nativas não se aplicam ao BPF.
  CLANG = "${pkgs.llvmPackages.clang-unwrapped}/bin/clang";
}
