# Só avalia configurações; não constrói nem ativa um sistema.
let
  nixpkgs = <nixpkgs>;
  lib = import (nixpkgs + "/lib");
  ativas = lib.filterAttrs (_: r: r.enable);
  avaliar = habilitado: (import (nixpkgs + "/nixos") {
    configuration = { ... }: {
      imports = [ ../modulo.nix ];
      services.controleAcesso.enable = habilitado;
      services.openssh.enable = true;
      services.xserver.enable = true;
      services.displayManager.sddm.enable = true;
      services.displayManager.autoLogin = { enable = true; user = "alice"; };
      users.users.alice.isNormalUser = true;
      system.stateVersion = "26.05";
    };
  }).config;
  c = avaliar true;
  desligado = avaliar false;
  servicos = [ "login" "sshd" "su" "sudo" ];
  verificar = nome:
    let r = c.security.pam.services.${nome}.rules.session;
        outras = builtins.attrValues (builtins.removeAttrs r [ "controle-reserva" "controle-confirma" ]);
    in assert r.controle-reserva.control == "requisite";
       assert r.controle-confirma.control == "requisite";
       assert builtins.all (x: r.controle-reserva.order < x.order && x.order < r.controle-confirma.order) outras;
       assert r.systemd.enable;
       true;
in
assert builtins.all verificar servicos;
assert builtins.all (nome: builtins.all (fase:
  ativas c.security.pam.services.${nome}.rules.${fase} == ativas desligado.security.pam.services.${nome}.rules.${fase}
) [ "auth" "account" "password" ]) servicos;
assert c.security.pam.services.sddm.rules.session.login.modulePath == "login";
assert c.security.pam.services.sddm-autologin.rules.session.sddm.modulePath == "sddm";
assert !(c.security.pam.services.sddm.rules.session ? controle-reserva);
assert !(c.security.pam.services.sddm-greeter.rules.session ? controle-reserva);
assert !(c.security.pam.services.kde.rules.session or {} ? controle-reserva);
assert !(desligado.security.pam.services.login.rules.session ? controle-reserva);
assert !(builtins.elem "d /run/controle-pam 0700 root root -" desligado.systemd.tmpfiles.rules);
{ resultado = "SSH, TTY, su, sudo, includes SDDM/autologin, ordem e desativação: OK"; }
