{
  description = "eBPF telemetry and access control on NixOS";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs, ... }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs {
        inherit system;
      };
    in {
      devShells.${system}.default = pkgs.mkShell {
        packages = with pkgs; [
          # eBPF
          llvmPackages.clang-unwrapped
          llvm
          bpftools
          bpftrace
          clang
          libbpf
          pahole
          elfutils
          tcpdump
          gcc

          # Development
          python3
          python3Packages.prometheus-client

          # Observability
          prometheus
          grafana

          # Useful tools
          iproute2
          gnumake
          pkg-config
        ];
      };
    };
}