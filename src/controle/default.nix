{ pkgs ? import <nixpkgs> { } }:
pkgs.stdenv.mkDerivation {
  pname = "pam-controle";
  version = "1.0";
  src = pkgs.lib.cleanSourceWith {
    src = ./.;
    filter = path: type: baseNameOf path != "build" && pkgs.lib.cleanSourceFilter path type;
  };
  nativeBuildInputs = [ pkgs.pkg-config ];
  buildInputs = [ pkgs.pam pkgs.systemd ];
  doCheck = true;
  nativeCheckInputs = [ pkgs.python3 pkgs.dbus ];
  checkPhase = "make test";
  installPhase = ''
    install -Dm755 build/pam_controle.so $out/lib/security/pam_controle.so
  '';
}
