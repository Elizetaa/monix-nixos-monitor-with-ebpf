"""Exercise the actual one-shot CLI with unprivileged executable helpers."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


class OneShotCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.proc = self.base / "proc"
        self.proc.mkdir()
        (self.proc / "stat").write_text(
            "cpu 10 0 0 10 0 0 0 0\ncpu0 10 0 0 10 0 0 0 0\n"
        )
        self.sys = self.base / "sys"
        self.sys.mkdir()
        self.helper = self.base / "cpu_helper.py"
        self.pid_file = self.base / "helper.pid"
        self.source_root = Path(__file__).resolve().parents[2]

    def run_once(self, helper_body):
        self.helper.write_text(
            f"#!{sys.executable}\n"
            "import json\nimport os\nfrom pathlib import Path\nimport time\n"
            f"Path({str(self.pid_file)!r}).write_text(str(os.getpid()))\n"
            + helper_body
        )
        self.helper.chmod(0o755)
        command = [
            sys.executable,
            "-m", "generated.exporter",
            "--once",
            "--collector", "cpu",
            "--ebpf", "required",
            "--interval", "0.05",
            "--cpu-helper", str(self.helper),
            "--hostname", "testhost",
            "--proc-root", str(self.proc),
            "--sys-root", str(self.sys),
        ]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(self.source_root)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            result = subprocess.run(
                command, cwd=self.base, env=environment,
                capture_output=True, text=True, timeout=6,
            )
        except subprocess.TimeoutExpired:
            # A failing CLI should not leave this test's helper running. Verify
            # its command line before signaling the PID saved by the fixture.
            if self.pid_file.exists():
                pid = int(self.pid_file.read_text())
                try:
                    command_line = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                    if os.fsencode(self.helper) in command_line:
                        os.kill(pid, signal.SIGTERM)
                except (FileNotFoundError, ProcessLookupError):
                    pass
            raise
        self.assertTrue(self.pid_file.exists(), result.stderr)
        pid = int(self.pid_file.read_text())
        with self.assertRaises(ProcessLookupError, msg="CLI must stop and reap its CPU helper"):
            os.kill(pid, 0)
        return result

    def test_required_helper_without_samples_fails_without_fabricated_usage(self):
        result = self.run_once("time.sleep(5)\n")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("helper has not produced a sample", result.stderr)
        self.assertNotIn("monix_cpu_usage_percent", result.stdout)

    def test_required_helper_with_samples_succeeds_and_is_cleaned_up(self):
        result = self.run_once(
            "while True:\n"
            "    print(json.dumps({'usage_percent': 37.5, 'cores': {'0': 37.5}}), flush=True)\n"
            "    time.sleep(0.05)\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('monix_cpu_ebpf_attached{hostname="testhost"} 1\n', result.stdout)
        self.assertIn(
            'monix_cpu_usage_percent{cpu="all",hostname="testhost",source="ebpf"} 37.5\n',
            result.stdout,
        )
        self.assertIn(
            'monix_cpu_usage_percent{cpu="0",hostname="testhost",source="ebpf"} 37.5\n',
            result.stdout,
        )
        self.assertNotIn('source="proc"', result.stdout)


if __name__ == "__main__":
    unittest.main()
