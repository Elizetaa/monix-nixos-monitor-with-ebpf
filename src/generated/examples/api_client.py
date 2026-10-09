#!/usr/bin/env python3
"""Fetch a real agent snapshot and explicitly export it to a local JSON file."""

import argparse
import json
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from generated.configuration import DEFAULT_HOST, DEFAULT_PORT, positive_interval


def fetch_snapshot(url: str, timeout: float = 5.0) -> dict:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("O endereço da API deve ser uma URL http ou https.")
    with urlopen(url, timeout=timeout) as response:
        if response.status != 200:
            raise ValueError(f"A API respondeu HTTP {response.status}.")
        if response.headers.get_content_type() != "application/json":
            raise ValueError("A API não retornou application/json.")
        snapshot = json.loads(response.read().decode("utf-8"))
    if not isinstance(snapshot, dict) or type(snapshot.get("schema_version")) is not int or snapshot["schema_version"] != 1:
        raise ValueError("Contrato JSON incompatível: esperado schema_version = 1.")
    if (not isinstance(snapshot.get("machine_id"), str) or not snapshot["machine_id"]
            or not isinstance(snapshot.get("timestamp"), str)
            or not isinstance(snapshot.get("meta"), dict) or snapshot["meta"].get("ready") is not True):
        raise ValueError("A resposta não contém uma coleta válida.")
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=f"http://{DEFAULT_HOST}:{DEFAULT_PORT}/api/metrics")
    parser.add_argument("--output", type=Path, default=Path("metrics.json"))
    parser.add_argument("--timeout", type=positive_interval, default=5.0)
    args = parser.parse_args(argv)
    try:
        snapshot = fetch_snapshot(args.url, args.timeout)
        args.output.write_text(json.dumps(snapshot, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
                               encoding="utf-8")
    except HTTPError as error:
        print(f"A API respondeu HTTP {error.code}: {error.reason}", file=sys.stderr)
        return 1
    except (URLError, OSError, ValueError) as error:
        print(f"Não foi possível consultar/exportar métricas: {error}", file=sys.stderr)
        return 1
    print(f"Snapshot salvo em {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
