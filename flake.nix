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
      vm = self.nixosConfigurations.monix-vm.config.system.build.vm;
    in {
      # VM de laboratório (Monix + Prometheus + Grafana): `nix run .#vm`
      nixosConfigurations.monix-vm = nixpkgs.lib.nixosSystem {
        inherit system;
        modules = [ ./nixos/monix-vm.nix ];
      };
      packages.${system}.vm = vm;
      apps.${system}.vm = {
        type = "app";
        program = "${vm}/bin/run-monix-vm-vm";
      };

      devShells.${system} = {
        telemetria = import ./src/telemetria/shell.nix { inherit pkgs; };
        default = pkgs.mkShell {
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
    };
}
