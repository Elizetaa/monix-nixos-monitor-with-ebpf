"""Leituras opcionais de sysfs: atributos ausentes não viram zero."""
import math


def texto(path):
    try:
        return path.read_text().strip()
    except (OSError, UnicodeError):
        return None


def numero(path):
    try:
        value = float(path.read_text().strip())
        return value if math.isfinite(value) else None
    except (OSError, UnicodeError, ValueError):
        return None
