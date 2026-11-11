{ config, lib, pkgs, ... }:
let
  cfg = config.services.controleAcesso;
  pacote = import ./default.nix { inherit pkgs; };
  regras = nome:
    let
      outras = lib.filterAttrs (n: _: !(lib.elem n [ "controle-reserva" "controle-confirma" ]))
        config.security.pam.services.${nome}.rules.session;
      ordens = map (r: r.order) (lib.attrValues outras);
      regra = fase: ordem: {
        order = ordem;
        control = "requisite";
        modulePath = "${pacote}/lib/security/pam_controle.so";
        args = [ fase "grupo=${cfg.grupoAdministrativo}" ];
      };
    in {
      startSession = true;
      rules.session = {
        controle-reserva = regra "reservar" ((lib.foldl' lib.min 10000 ordens) - 10);
        controle-confirma = regra "confirmar" ((lib.foldl' lib.max 10000 ordens) + 10);
      };
    };
in {
  options.services.controleAcesso = {
    enable = lib.mkEnableOption "exclusividade de um UID comum nas sessões PAM selecionadas";
    grupoAdministrativo = lib.mkOption { type = lib.types.str; default = "wheel"; };
    servicos = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "login" "sshd" "su" "sudo" ];
      description = "Pilhas diretas; não incluir pilhas que já incluam outra protegida.";
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [ {
      assertion = builtins.match "[a-zA-Z0-9_-]+" cfg.grupoAdministrativo != null;
      message = "controleAcesso: nome de grupo inválido";
    } {
      assertion = !(lib.any (s: lib.elem s cfg.servicos) [ "sddm" "sddm-autologin" ]);
      message = "Proteja login: no NixOS, sddm inclui login e sddm-autologin inclui sddm.";
    } {
      assertion = !config.services.displayManager.sddm.enable || (
        lib.elem "login" cfg.servicos &&
        config.security.pam.services.sddm.rules.session.login.control == "include" &&
        config.security.pam.services.sddm.rules.session.login.modulePath == "login" &&
        config.security.pam.services.sddm-autologin.rules.session.sddm.control == "include" &&
        config.security.pam.services.sddm-autologin.rules.session.sddm.modulePath == "sddm"
      );
      message = "controleAcesso: SDDM precisa incluir login e autologin precisa incluir sddm; revise as pilhas.";
    } ];
    systemd.tmpfiles.rules = [ "d /run/controle-pam 0700 root root -" ];
    security.pam.services = lib.genAttrs cfg.servicos regras;
  };
}
