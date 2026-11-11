# Integração opcional com logind, SSH, getty e SDDM reais em QEMU.
{ pkgs ? import <nixpkgs> { } }:
pkgs.testers.runNixOSTest {
  name = "controle-pam";
  nodes.machine = { ... }: {
    imports = [ ../modulo.nix ];
    services.controleAcesso.enable = true;
    services.openssh = { enable = true; settings.PasswordAuthentication = true; };
    services.xserver = { enable = true; desktopManager.xterm.enable = true; };
    services.displayManager = {
      sddm.enable = true;
      autoLogin = { enable = true; user = "alice"; };
      defaultSession = "xterm";
    };
    users.users = {
      alice = { isNormalUser = true; password = "teste"; };
      bob = { isNormalUser = true; password = "teste"; };
      admin = { isNormalUser = true; password = "teste"; extraGroups = [ "wheel" ]; };
    };
    security.sudo.wheelNeedsPassword = false;
    environment.systemPackages = [ pkgs.sshpass ];
    virtualisation = { memorySize = 2048; cores = 2; };
    system.stateVersion = "26.05";
  };
  testScript = ''
    start_all()
    machine.wait_for_unit("sshd.service")
    machine.wait_for_unit("display-manager.service")
    machine.wait_until_succeeds("pgrep -u alice xterm")

    def ssh(user, command="true"):
        return ("sshpass -p teste ssh -o StrictHostKeyChecking=no "
                "-o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 "
                f"-tt {user}@localhost '{command}'")

    # Autologin SDDM ocupa alice; SSH do mesmo UID e admin continuam possíveis.
    machine.succeed(ssh("alice"))
    machine.fail(ssh("bob"))
    machine.succeed(ssh("admin"))
    machine.succeed(ssh("admin", "sudo -u root true"))
    machine.fail(ssh("admin", "sudo -u bob true"))
    machine.fail("su bob -c true")

    # tty3 evita o tty2 usado pelo X/SDDM; mesmo UID mantém TTY e gráfico.
    machine.succeed("systemctl start getty@tty3.service; chvt 3")
    machine.wait_until_tty_matches("3", "login: ", timeout=60)
    machine.send_chars("alice\n")
    machine.wait_until_tty_matches("3", "Password:", timeout=60)
    machine.send_chars("teste\n")
    machine.send_chars("touch /tmp/controle-tty\n")
    machine.wait_until_succeeds("test -f /tmp/controle-tty")
    machine.fail(ssh("bob"))

    # Parar o gráfico não libera o UID enquanto a sessão TTY permanecer aberta.
    machine.succeed("systemctl stop display-manager.service")
    machine.fail(ssh("bob"))
    machine.send_chars("exit\n")
    machine.wait_until_succeeds(ssh("bob"))
  '';
}
