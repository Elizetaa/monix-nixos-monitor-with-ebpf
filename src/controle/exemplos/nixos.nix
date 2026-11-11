# Copiar/importar este exemplo apenas em uma VM de laboratório inicialmente.
{ ... }: {
  imports = [ ../modulo.nix ];
  services.controleAcesso = {
    enable = true;
    grupoAdministrativo = "wheel";
    # SDDM inclui login; sddm-autologin inclui sddm. Não duplicar as regras.
    servicos = [ "sshd" "login" "su" "sudo" ];
  };
  # Para outro grupo: crie-o com users.groups e associe os administradores
  # via users.users.<nome>.extraGroups ou group (grupo primário).
}
