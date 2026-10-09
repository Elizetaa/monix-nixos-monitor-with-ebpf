"""Central configuration for the local agent; no external Python dependencies."""

import argparse
from dataclasses import dataclass, replace
import math
from pathlib import Path
import re
import tomllib


DEFAULT_INTERVAL = 5.0
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9108
COLLECTORS = ("cpu", "gpu", "memory", "storage", "thermal")
SNAPSHOT_CATEGORIES = ("cpu", "gpu", "memory", "storage", "motherboard", "thermal")


def validate_interval(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("O intervalo deve ser um número em segundos.")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError("O intervalo deve ser finito.") from error
    if not math.isfinite(number) or not 0 < number <= 86400:
        raise ValueError("O intervalo deve ser finito, maior que zero e no máximo 86400 segundos.")
    return number


def validate_port(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError("A porta deve ser um inteiro entre 1 e 65535.")
    return value


def validate_ambient_sensors(value: object) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError("ambient_sensors deve ser uma lista de identificadores CHIP:TEMPN.")
    for selector in value:
        if not isinstance(selector, str):
            raise ValueError("ambient_sensors deve conter strings CHIP:TEMPN.")
        chip, separator, sensor = selector.rpartition(":")
        if not separator or not chip or not re.fullmatch(r"temp[1-9][0-9]*", sensor):
            raise ValueError("ambient_sensors deve conter identificadores CHIP:TEMPN, como nct6798:temp1.")
    return tuple(value)


def positive_interval(value: str) -> float:
    """Argparse adapter shared by the CLI and examples."""
    try:
        return validate_interval(float(value))
    except (ValueError, OverflowError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def valid_port(value: str) -> int:
    try:
        return validate_port(int(value))
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


@dataclass(frozen=True)
class AgentConfig:
    interval_seconds: float = DEFAULT_INTERVAL
    machine_id: str | None = None
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    ebpf: str = "auto"
    cpu_helper: Path | None = None
    ambient_sensors: tuple[str, ...] = ()
    collectors: tuple[str, ...] | None = None
    prometheus_enabled: bool = False

    def __post_init__(self) -> None:
        validate_interval(self.interval_seconds)
        validate_port(self.port)
        if self.machine_id is not None and (
            not isinstance(self.machine_id, str) or not self.machine_id.strip()
        ):
            raise ValueError("machine_id deve ser uma string não vazia.")
        if not isinstance(self.host, str) or not self.host.strip():
            raise ValueError("O endereço da API deve ser uma string não vazia.")
        if self.ebpf not in ("auto", "required", "disabled"):
            raise ValueError("ebpf deve ser auto, required ou disabled.")
        if self.cpu_helper is not None and not isinstance(self.cpu_helper, Path):
            raise ValueError("cpu_helper deve ser um caminho.")
        if not isinstance(self.ambient_sensors, tuple):
            raise ValueError("ambient_sensors deve ser uma tupla de identificadores CHIP:TEMPN.")
        validate_ambient_sensors(self.ambient_sensors)
        if self.collectors is not None and (
            not isinstance(self.collectors, tuple) or not self.collectors
            or any(name not in COLLECTORS for name in self.collectors)
            or len(set(self.collectors)) != len(self.collectors)
        ):
            raise ValueError("collectors deve conter categorias únicas: " + ", ".join(COLLECTORS))
        if type(self.prometheus_enabled) is not bool:
            raise ValueError("prometheus.enabled deve ser booleano.")


def load_config(path: str | Path | None = None) -> AgentConfig:
    """Read a TOML file explicitly selected by the caller, or use defaults."""
    if path is None:
        return AgentConfig()
    config_path = Path(path).expanduser().absolute()
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    allowed = {
        "monitor": {"interval_seconds", "machine_id", "ebpf", "cpu_helper", "ambient_sensors", "collectors"},
        "api": {"host", "port"},
        "prometheus": {"enabled"},
    }
    unknown = data.keys() - allowed.keys()
    if unknown:
        raise ValueError("Seções desconhecidas: " + ", ".join(sorted(unknown)))
    for section, keys in allowed.items():
        table = data.get(section, {})
        if not isinstance(table, dict):
            raise ValueError(f"{section} deve ser uma tabela TOML.")
        unexpected = table.keys() - keys
        if unexpected:
            raise ValueError(f"Chaves desconhecidas em {section}: " + ", ".join(sorted(unexpected)))
    monitor = data.get("monitor", {})
    changes = dict(monitor)
    for name in ("ambient_sensors", "collectors"):
        if name in changes:
            if not isinstance(changes[name], list) or any(
                not isinstance(value, str) for value in changes[name]
            ):
                raise ValueError(f"{name} deve ser uma lista de strings.")
            changes[name] = tuple(changes[name])
    if "cpu_helper" in changes:
        helper = changes["cpu_helper"]
        if not isinstance(helper, str) or not helper.strip():
            raise ValueError("cpu_helper deve ser um caminho não vazio.")
        helper_path = Path(helper).expanduser()
        changes["cpu_helper"] = helper_path if helper_path.is_absolute() else config_path.parent / helper_path
    changes.update(data.get("api", {}))
    if "enabled" in data.get("prometheus", {}):
        changes["prometheus_enabled"] = data["prometheus"]["enabled"]
    return replace(AgentConfig(), **changes)
