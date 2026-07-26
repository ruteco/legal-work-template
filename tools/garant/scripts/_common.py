"""Общие утилиты для CLI-скриптов tools/garant/scripts."""

from __future__ import annotations

import json
import sys

from garant_client import GarantAPIError, GarantClient


def get_client() -> GarantClient:
    try:
        return GarantClient()
    except RuntimeError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)


def print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def run(func) -> None:
    """Вызвать func() и аккуратно обработать ошибки API/аргументов."""
    try:
        func()
    except GarantAPIError as exc:
        print(f"Ошибка API ГАРАНТ ({exc.status_code}): {exc.payload}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
