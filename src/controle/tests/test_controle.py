"""PAM real em confdir privada; NSS/logind simulados; flock real entre processos."""
import os
from pathlib import Path
import select
import shutil
import subprocess
import time
import unittest

BASE = Path(__file__).resolve().parents[1]
BUILD = BASE / "build"
UIDS = {"root": 0, "alice": 1001, "alias": 1001, "bob": 1002,
        "admin": 2000, "primario": 2001, "inexistente": 9000}


class Controle(unittest.TestCase):
    def setUp(self):
        self.area = BUILD / "laboratorio"
        self.area.mkdir(exist_ok=True)
        self.estado = BUILD / "estado"
        self.estado.mkdir(mode=0o700, exist_ok=True)
        self.estado.chmod(0o700)
        self.state = self.area / "sessoes"
        self.state.write_text("")
        self.processos = []
        self.pilha()

    def pilha(self, grupo="wheel", antes="", depois="", confirmar=True):
        modulo = BUILD / "pam_teste.so"
        self.area.joinpath("controle").write_text(
            f"session requisite {modulo} reservar grupo={grupo}\n"
            + antes
            + f"session required {BUILD / 'pam_registro.so'}\n"
            + (f"session requisite {modulo} confirmar grupo={grupo}\n" if confirmar else "")
            + depois
        )

    def abrir(self, nome, **variaveis):
        env = dict(os.environ, CONTROLE_TEST_STATE=str(self.state),
                   CONTROLE_TEST_UID=str(UIDS[nome]))
        env.update({"CONTROLE_TEST_" + k: str(v) for k, v in variaveis.items()})
        p = subprocess.Popen([str(BUILD / "cliente"), str(self.area), "controle", nome],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=env)
        self.processos.append(p)
        return p

    def resultado(self, p):
        self.assertTrue(select.select([p.stdout], [], [], 8)[0], "PAM travou")
        linha = p.stdout.readline()
        self.assertTrue(linha, "cliente terminou sem resultado")
        return int(linha)

    def fechar(self, p, comando="c"):
        p.communicate(comando, timeout=8)
        self.assertEqual(p.returncode, 0)

    def entrar(self, nome, esperado=0, **variaveis):
        p = self.abrir(nome, **variaveis)
        r = self.resultado(p)
        if esperado == 0:
            self.assertEqual(r, 0)
        else:
            self.assertNotEqual(r, 0)
        return p

    def tearDown(self):
        for p in self.processos:
            if p.poll() is None:
                p.kill()
            p.communicate()
        self.estado.chmod(0o700)
        for path in self.estado.iterdir():
            path.unlink()
        shutil.rmtree(self.area)

    def test_exclusividade_multiplas_sessoes_uid_e_alias(self):
        a = self.entrar("alice")
        a2 = self.entrar("alias")
        b = self.entrar("bob", esperado=1)
        self.fechar(b, "e")
        self.fechar(a)
        b = self.entrar("bob", esperado=1)
        self.fechar(b, "e")
        self.fechar(a2)
        self.fechar(self.entrar("bob"))

    def test_administradores_nao_ocupam_nem_liberam(self):
        admins = [self.entrar(n) for n in ("root", "admin", "primario")]
        a = self.entrar("alice")
        for p in admins:
            self.fechar(p)
        self.fechar(self.entrar("bob", esperado=1), "e")
        self.fechar(a)
        self.fechar(self.entrar("bob"))

    def test_administradores_entram_com_comum_presente(self):
        self.entrar("alice")
        for nome in ("root", "admin", "primario"):
            self.fechar(self.entrar(nome))

    def test_conta_destino_nao_operador(self):
        self.entrar("alice")
        self.fechar(self.entrar("bob", esperado=1), "e")
        self.fechar(self.entrar("root"))

    def test_sessoes_anteriores_ao_modulo_e_tela_bloqueada(self):
        # Lock de tela não remove registros do logind: permanece o mesmo snapshot.
        self.state.write_text("1001 99999 0\n1001 99998 0\n")
        self.fechar(self.entrar("bob", esperado=1), "e")
        self.fechar(self.entrar("alice"))

    def test_conflito_preexistente_nao_encerra_sessoes(self):
        self.state.write_text("1001 888 0\n1002 889 0\n")
        self.fechar(self.entrar("alice", esperado=1), "e")
        self.assertEqual(len(self.state.read_text().splitlines()), 2)
        self.fechar(self.entrar("admin"))

    def test_negacao_fatal_para_sudo(self):
        self.entrar("alice")
        p = self.abrir("bob")
        # Linux-PAM: PAM_PERM_DENIED=6; sudo ignora PAM_SESSION_ERR=14.
        self.assertEqual(self.resultado(p), 6)
        self.fechar(p, "e")

    def test_concorrencia_dois_uids(self):
        a = self.abrir("alice", DELAY=250)
        b = self.abrir("bob", DELAY=250)
        resultados = [self.resultado(p) for p in (a, b)]
        self.assertEqual(sum(r == 0 for r in resultados), 1)
        for p, r in zip((a, b), resultados):
            self.fechar(p, "c" if r == 0 else "e")

    def test_concorrencia_mesmo_uid(self):
        a = self.abrir("alice", DELAY=100)
        b = self.abrir("alias", DELAY=100)
        self.assertEqual([self.resultado(p) for p in (a, b)], [0, 0])
        self.fechar(a)
        self.fechar(b)

    def test_tentativa_abandonada_pam_end(self):
        # Reserva com sucesso, pilha incompleta: pam_end libera sem logout.
        self.pilha(confirmar=False)
        self.fechar(self.entrar("alice", NO_REGISTER=1), "e")
        self.pilha()
        self.fechar(self.entrar("bob"))

    def test_morte_durante_abertura_libera_flock(self):
        marca = self.area / "marca"
        p = self.abrir("alice", DELAY=10000, MARK=marca)
        limite = time.monotonic() + 5
        while not marca.exists() and time.monotonic() < limite:
            time.sleep(0.01)
        self.assertTrue(marca.exists())
        p.kill()
        p.communicate(timeout=3)
        self.fechar(self.entrar("bob"))

    def test_morte_apos_admissao_e_reconciliacao_logind(self):
        p = self.entrar("alice")
        p.kill()
        p.communicate(timeout=3)
        # Escopo ainda em closing permanece ocupado. Logind remove quando termina.
        self.fechar(self.entrar("bob", esperado=1), "e")
        self.state.write_text("")
        self.fechar(self.entrar("bob"))

    def test_falha_posterior_e_limpeza(self):
        # Outra verificação PAM continua obrigatória, inclusive para root.
        self.pilha(depois=f"session required {BUILD / 'pam_teste.so'} invalida\n")
        a = self.entrar("alice", esperado=1)
        self.fechar(a)
        self.fechar(self.entrar("root", esperado=1))
        self.pilha()
        self.fechar(self.entrar("bob"))

    def test_pam_systemd_ausente_ou_sessao_outro_uid(self):
        self.fechar(self.entrar("alice", esperado=1, NO_REGISTER=1), "e")
        self.state.write_text("0 123 1\n")
        self.fechar(self.entrar("alice", esperado=1, NO_REGISTER=1), "e")

    def test_sessao_aninhada_mesmo_uid(self):
        self.state.write_text("1001 123 1\n")
        self.fechar(self.entrar("alice", NO_REGISTER=1))

    def test_falhas_logind_e_nss_preservam_admin_seguro(self):
        self.fechar(self.entrar("alice", esperado=1, LOGIND_FAIL=1), "e")
        for nome in ("root", "admin", "primario"):
            self.fechar(self.entrar(nome, LOGIND_FAIL=1))
        self.fechar(self.entrar("alice", esperado=1, NSS_FAIL=1), "e")
        self.fechar(self.entrar("root", NSS_FAIL=1))

    def test_grupo_configuravel_inexistente(self):
        self.pilha(grupo="ausente")
        self.fechar(self.entrar("alice", esperado=1), "e")
        self.fechar(self.entrar("root"))

    def test_identidade_desconhecida_falha_fechada(self):
        self.fechar(self.entrar("inexistente", esperado=1), "e")
        self.state.write_text("9876 123 0\n")
        self.fechar(self.entrar("alice", esperado=1), "e")
        self.fechar(self.entrar("admin"))

    def test_estado_permissoes_e_symlink(self):
        self.estado.chmod(0o777)
        self.fechar(self.entrar("alice", esperado=1), "e")
        self.fechar(self.entrar("root"))
        self.estado.chmod(0o700)
        lock = self.estado / "admissao.lock"
        if lock.exists():
            lock.unlink()
        lock.symlink_to(self.state)
        self.fechar(self.entrar("alice", esperado=1), "e")
        lock.unlink()
        self.fechar(self.entrar("bob"))

    def test_reinicio_sem_registros_proprios(self):
        (self.estado / "admissao.lock").touch(mode=0o600)
        self.fechar(self.entrar("bob"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
