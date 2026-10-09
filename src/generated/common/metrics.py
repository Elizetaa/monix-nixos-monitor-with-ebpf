"""Exposição Prometheus 0.0.4, sem dependências Python externas."""

from dataclasses import dataclass, field
import math
import re
from typing import Iterable


@dataclass(frozen=True)
class Sample:
    name: str
    help: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)


def _escape_label(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def validate_samples(samples: Iterable[Sample], hostname: str) -> list[Sample]:
    """Valida séries e copia labels antes de compartilhar um snapshot."""
    validated: list[Sample] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for sample in samples:
        if not re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", sample.name):
            raise ValueError(f"Nome de métrica inválido: {sample.name!r}")
        if not math.isfinite(sample.value):
            continue
        labels = {key: str(value) for key, value in sample.labels.items()}
        for key in labels:
            if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", key) or key.startswith("__"):
                raise ValueError(f"Label inválido: {key!r}")
        identity = (sample.name, tuple(sorted({**labels, "hostname": hostname}.items())))
        if identity in seen:
            raise ValueError(f"Série duplicada: {identity!r}")
        seen.add(identity)
        validated.append(Sample(sample.name, sample.help, sample.value, labels))
    return validated


def render(samples: Iterable[Sample], hostname: str) -> bytes:
    families: dict[str, list[Sample]] = {}
    for sample in validate_samples(samples, hostname):
        families.setdefault(sample.name, []).append(sample)

    lines: list[str] = []
    for name, family in sorted(families.items()):
        description = family[0].help.replace("\\", "\\\\").replace("\n", "\\n")
        lines.extend((f"# HELP {name} {description}", f"# TYPE {name} gauge"))
        for sample in family:
            labels = {**sample.labels, "hostname": hostname}
            label_text = ",".join(f'{key}="{_escape_label(value)}"' for key, value in sorted(labels.items()))
            lines.append(f"{name}{{{label_text}}} {sample.value:.17g}")
    return ("\n".join(lines) + "\n").encode("utf-8")
