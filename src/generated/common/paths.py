"""Caminhos injetáveis para permitir testes sem acessar o hardware."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SystemPaths:
    proc: Path = Path("/proc")
    sys: Path = Path("/sys")


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return default


def read_number(path: Path) -> float | None:
    try:
        return float(read_text(path))
    except ValueError:
        return None
