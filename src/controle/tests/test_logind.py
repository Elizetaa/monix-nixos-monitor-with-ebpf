"""Leitor original contra login1 simulado em D-Bus privado, sem tocar no host."""
import os
from pathlib import Path
import select
import subprocess
import unittest
from urllib.parse import quote

BUILD = Path(__file__).resolve().parents[1] / "build"


class Logind(unittest.TestCase):
    def setUp(self):
        runtime = BUILD / "runtime"
        runtime.mkdir(mode=0o700, exist_ok=True)
        self.bus = subprocess.Popen(
            ["dbus-daemon", "--config-file=" + str(Path(__file__).with_name("dbus.conf")),
             "--nofork", "--print-address=1",
             "--address=unix:path=" + quote(str(BUILD / "bus.sock"), safe="/")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=dict(os.environ, XDG_RUNTIME_DIR=str(runtime)))
        self.assertTrue(select.select([self.bus.stdout], [], [], 3)[0])
        endereco = self.bus.stdout.readline().strip()
        if not endereco:
            self.fail(self.bus.stderr.read())
        self.env = dict(os.environ, DBUS_SYSTEM_BUS_ADDRESS=endereco)
        self.server = None

    def tearDown(self):
        for p in (self.server, self.bus):
            if p:
                p.terminate()
                p.communicate(timeout=3)

    def ler(self, classe):
        self.server = subprocess.Popen([str(BUILD / "logind")],
                env=dict(self.env, CLASSE=classe), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True)
        self.assertTrue(select.select([self.server.stdout], [], [], 3)[0])
        linha = self.server.stdout.readline().strip()
        if not linha:
            self.fail(self.server.stderr.read())
        self.assertEqual(linha, "pronto")
        return subprocess.run([str(BUILD / "leitor")], env=self.env,
                              capture_output=True, text=True, timeout=3)

    def test_usuario(self):
        r = self.ler("user")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "1 1001:1234")

    def test_usuario_light(self):
        self.assertEqual(self.ler("user-light").stdout.strip(), "1 1001:1234")

    def test_greeter(self):
        self.assertEqual(self.ler("greeter").stdout.strip(), "0")

    def test_classe_desconhecida(self):
        self.assertNotEqual(self.ler("classe-invalida").returncode, 0)

    def test_logind_ausente(self):
        r = subprocess.run([str(BUILD / "leitor")], env=self.env, timeout=3)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
