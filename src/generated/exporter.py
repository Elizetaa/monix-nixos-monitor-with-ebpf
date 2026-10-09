#!/usr/bin/env python3
"""Compatibility entry point: the same Monitor, with Prometheus enabled by default."""

from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generated.agent import main


if __name__ == "__main__":
    raise SystemExit(main(legacy=True))
