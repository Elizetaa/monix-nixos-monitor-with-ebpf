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
          bpftrace
          bcc
          clang
          llvm
          libbpf

          # Development
          python3
          python3Packages.prometheus-client

          # Observability
          prometheus
          grafana

          # Useful tools
          iproute2
          bpftools
          jq
          curl
        ];

        shellHook = ''
          echo "eBPF development environment"
          echo
          echo "Available:"
          echo "  bpftrace  - eBPF tracing"
          echo "  bcc       - BPF Compiler Collection"
          echo "  clang     - eBPF compiler"
          echo "  prometheus - metrics storage"
          echo "  grafana   - dashboards"
        '';
      };
    };
}