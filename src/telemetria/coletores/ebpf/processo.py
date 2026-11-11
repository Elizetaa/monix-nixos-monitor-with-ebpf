"""Ciclo de vida e leitura assíncrona dos dois loaders, sem fallback de métricas."""
from collections import deque
import subprocess
import threading
import time

from ...dados import resultado


class Processo:
    def __init__(self, interval, helper, source):
        self.interval = interval
        self.helper, self.source = helper, source
        self.process = None
        self.latest = None
        self.failure = None
        self.stderr = deque(maxlen=10)
        self.lock = threading.Lock()
        self.threads = []
        self.closed = False
        self.started = time.monotonic()
        try:
            self.process = subprocess.Popen(
                [str(self.helper), "--interval", str(interval)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        except OSError as exc:
            self.failure = str(exc)
            return
        for stream, target in ((self.process.stdout, self._stdout), (self.process.stderr, self._stderr)):
            thread = threading.Thread(target=target, args=(stream,), daemon=True)
            self.threads.append(thread)
            thread.start()

    def _stdout(self, stream):
        try:
            for line in stream:
                payload = self.decode(line)
                with self.lock:
                    self.latest = (time.monotonic(), payload)
        except (ValueError, OSError, UnicodeError) as exc:
            with self.lock:
                self.failure = str(exc)
        finally:
            with self.lock:
                self.failure = self.failure or "Loader encerrou stdout"

    def _stderr(self, stream):
        try:
            for line in stream:
                with self.lock:
                    self.stderr.append(line.strip()[-1000:])
        except (OSError, UnicodeError) as exc:
            with self.lock:
                self.stderr.append(str(exc))

    def collect(self):
        with self.lock:
            latest, failure, messages = self.latest, self.failure, list(self.stderr)
        code = self.process.poll() if self.process else None
        if code is not None:
            failure = f"Loader encerrou com código {code}"
        timeout = max(1, 3 * self.interval)
        now = time.monotonic()
        if not failure and latest and now - latest[0] <= timeout:
            return resultado(self.source, latest[1])
        if not failure and now - (latest[0] if latest else self.started) <= timeout:
            return resultado(self.source, status="pending", error="Aguardando amostra do loader")
        reason = failure or "Loader sem amostra recente"
        if messages:
            reason += ": " + " | ".join(messages)
        return resultado(self.source, status="error", error=reason)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process:
            if self.process.poll() is None:
                try:
                    self.process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            for thread in self.threads:
                thread.join(timeout=1)
            self.process.stdout.close()
            self.process.stderr.close()
